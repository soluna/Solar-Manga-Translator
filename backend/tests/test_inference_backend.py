from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import traceback
import unittest
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest import mock

import numpy as np


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from backend.inference_backend import (
    InferenceBackend,
    InferenceMalformedOutputError,
    InferenceRequest,
    InferenceResult,
    InferenceRuntimeError,
    InferenceUpstreamError,
    UpstreamInferenceBackend,
)
from engine.translator import TranslatorEngine
from runtime_paths import AppPaths


def _test_paths(root: Path) -> AppPaths:
    return AppPaths(
        code_dir=BACKEND_DIR,
        app_data_dir=root / "app-data",
        models_dir=root / "models",
        output_dir=root / "output",
        logs_dir=root / "logs",
        cache_dir=root / "cache",
        config_dir=root / "config",
    )


class _CompletedProcess:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode: int | None = returncode

    async def wait(self) -> int:
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


class _ReturncodeProcess:
    def __init__(self, wait_result: Any, process_returncode: Any) -> None:
        self.wait_result = wait_result
        self.returncode = process_returncode

    async def wait(self) -> Any:
        return self.wait_result

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


class _HangingProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.waiting = asyncio.Event()
        self.finished: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self.actions: list[str] = []

    async def wait(self) -> int:
        self.waiting.set()
        return await asyncio.shield(self.finished)

    def terminate(self) -> None:
        self.actions.append("terminate")

    def kill(self) -> None:
        self.actions.append("kill")
        self.returncode = -9
        if not self.finished.done():
            self.finished.set_result(-9)


class _FailingWaitProcess:
    def __init__(self, failure: BaseException) -> None:
        self.failure = failure
        self.returncode: int | None = None
        self.actions: list[str] = []

    async def wait(self) -> int:
        raise self.failure

    def terminate(self) -> None:
        self.actions.append("terminate")

    def kill(self) -> None:
        self.actions.append("kill")
        self.returncode = -9


class InferenceBackendTests(unittest.IsolatedAsyncioTestCase):
    def test_request_and_result_repr_do_not_expose_paths(self) -> None:
        root = Path("/private/application/secret-token")
        output = root / "output" / "page.png"
        request = InferenceRequest(
            source_dir=root / "source",
            output_dir=output.parent,
            config_path=root / "config.json",
            log_path=root / "inference.log",
            model_dir=root / "models",
            required_outputs=(output,),
            font_path=str(root / "font.ttf"),
        )
        result = InferenceResult(outputs=(output,), exit_code=0)

        self.assertNotIn(str(root), repr(request))
        self.assertNotIn(str(root), repr(result))

    async def test_success_returns_validated_domain_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = root / "source"
            output_dir = root / "output"
            model_dir = root / "models"
            source_dir.mkdir()
            output_dir.mkdir()
            config_path = root / "config.json"
            config_path.write_text("{}", encoding="utf-8")
            expected_output = output_dir / "page.png"
            spawned: list[tuple[tuple[str, ...], dict[str, Any]]] = []

            async def spawn(*command: str, **kwargs: Any) -> _CompletedProcess:
                spawned.append((command, kwargs))
                expected_output.write_bytes(b"translated")
                return _CompletedProcess()

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=lambda _request: True,
            )
            request = InferenceRequest(
                source_dir=source_dir,
                output_dir=output_dir,
                config_path=config_path,
                log_path=root / "inference.log",
                model_dir=model_dir,
                required_outputs=(expected_output,),
            )

            result = await backend.run(request)

            self.assertEqual(
                result,
                InferenceResult(outputs=(expected_output,), exit_code=0),
            )
            self.assertEqual(len(spawned), 1)
            command, options = spawned[0]
            self.assertIn("local", command)
            self.assertEqual(options["cwd"], str(root / "manga-image-translator"))

    async def test_success_exit_with_missing_required_output_is_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            async def spawn(*_command: str, **_kwargs: Any) -> _CompletedProcess:
                return _CompletedProcess()

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=lambda _request: True,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=root / "output",
                config_path=root / "config.json",
                log_path=root / "inference.log",
                model_dir=root / "models",
                required_outputs=(root / "output" / "missing.png",),
            )

            with self.assertRaises(InferenceMalformedOutputError) as raised:
                await backend.run(request)

            self.assertEqual(raised.exception.category, "malformed_output")
            self.assertNotIn(str(root), str(raised.exception))

    async def test_unchanged_stale_required_output_is_rejected_and_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output" / "page.png"
            output.parent.mkdir()
            output.write_bytes(b"OLD")

            async def spawn(*_command: str, **_kwargs: Any) -> _CompletedProcess:
                return _CompletedProcess()

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=lambda _request: True,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=output.parent,
                config_path=root / "config.json",
                log_path=root / "inference.log",
                model_dir=root / "models",
                required_outputs=(output,),
            )

            with self.assertRaises(InferenceMalformedOutputError):
                await backend.run(request)

            self.assertEqual(output.read_bytes(), b"OLD")

    async def test_rewriting_required_output_with_same_content_counts_as_current_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output" / "page.png"
            output.parent.mkdir()
            output.write_bytes(b"SAME")

            async def spawn(*_command: str, **_kwargs: Any) -> _CompletedProcess:
                output.write_bytes(b"SAME")
                return _CompletedProcess()

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=lambda _request: True,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=output.parent,
                config_path=root / "config.json",
                log_path=root / "inference.log",
                model_dir=root / "models",
                required_outputs=(output,),
            )

            result = await backend.run(request)

            self.assertEqual(result.outputs, (output,))
            self.assertEqual(output.read_bytes(), b"SAME")

    async def test_partial_required_outputs_are_rejected_and_previous_set_is_restored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "output" / "first.png"
            second = root / "output" / "second.png"
            first.parent.mkdir()
            first.write_bytes(b"OLD-FIRST")
            second.write_bytes(b"OLD-SECOND")

            async def spawn(*_command: str, **_kwargs: Any) -> _CompletedProcess:
                first.write_bytes(b"NEW-FIRST")
                return _CompletedProcess()

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=lambda _request: True,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=first.parent,
                config_path=root / "config.json",
                log_path=root / "inference.log",
                model_dir=root / "models",
                required_outputs=(first, second),
            )

            with self.assertRaises(InferenceMalformedOutputError):
                await backend.run(request)

            self.assertEqual(first.read_bytes(), b"OLD-FIRST")
            self.assertEqual(second.read_bytes(), b"OLD-SECOND")

    async def test_invalid_process_result_shape_is_malformed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            async def spawn(*_command: str, **_kwargs: Any) -> object:
                return object()

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=lambda _request: True,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=root / "output",
                config_path=root / "config.json",
                log_path=root / "inference.log",
                model_dir=root / "models",
            )

            with self.assertRaises(InferenceMalformedOutputError):
                await backend.run(request)

    async def test_nonzero_exit_is_classified_without_log_secret_or_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = "vendor-secret-token"

            async def spawn(*_command: str, **kwargs: Any) -> _CompletedProcess:
                kwargs["stdout"].write(
                    f"Traceback: failed under {root} using {secret}\n".encode()
                )
                kwargs["stdout"].flush()
                return _CompletedProcess(returncode=23)

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=lambda _request: True,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=root / "output",
                config_path=root / "config.json",
                log_path=root / "inference.log",
                model_dir=root / "models",
            )

            with self.assertRaises(InferenceUpstreamError) as raised:
                await backend.run(request)

            error = raised.exception
            self.assertEqual(error.category, "upstream_failure")
            self.assertEqual(error.exit_code, 23)
            self.assertNotIn(secret, str(error))
            self.assertNotIn(str(root), str(error))

    async def test_returncode_must_be_matching_exact_integers(self) -> None:
        malformed_pairs = (
            (True, True),
            ("0", "0"),
            (None, None),
            (0, None),
            (0, 1),
        )
        for wait_result, process_returncode in malformed_pairs:
            with self.subTest(
                wait_result=wait_result,
                process_returncode=process_returncode,
            ):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    secret = "returncode-secret"

                    async def spawn(
                        *_command: str,
                        **_kwargs: Any,
                    ) -> _ReturncodeProcess:
                        return _ReturncodeProcess(wait_result, process_returncode)

                    backend = UpstreamInferenceBackend(
                        base_dir=root,
                        process_factory=spawn,
                        runtime_preparer=lambda _request: True,
                    )
                    request = InferenceRequest(
                        source_dir=root / secret / "source",
                        output_dir=root / "output",
                        config_path=root / "config.json",
                        log_path=root / "inference.log",
                        model_dir=root / "models",
                    )

                    with self.assertRaises(InferenceMalformedOutputError) as raised:
                        await backend.run(request)

                    self.assertEqual(raised.exception.category, "malformed_output")
                    self.assertNotIn(secret, str(raised.exception))
                    self.assertNotIn(str(root), str(raised.exception))

    async def test_cancellation_terminates_then_kills_and_reraises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            process = _HangingProcess()
            output = root / "output" / "page.png"
            output.parent.mkdir()
            output.write_bytes(b"OLD")

            async def spawn(*_command: str, **_kwargs: Any) -> _HangingProcess:
                output.write_bytes(b"NEW")
                return process

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=lambda _request: True,
                termination_grace_seconds=0,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=root / "output",
                config_path=root / "config.json",
                log_path=root / "inference.log",
                model_dir=root / "models",
                required_outputs=(output,),
            )
            task = asyncio.create_task(backend.run(request))
            await process.waiting.wait()

            task.cancel()

            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(process.actions, ["terminate", "kill"])
            self.assertEqual(output.read_bytes(), b"OLD")

    async def test_cancellation_restore_failure_is_runtime_error_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = "cancel-restore-secret"
            process = _HangingProcess()
            output = root / "output" / "page.png"
            output.parent.mkdir()
            output.write_bytes(b"OLD")

            async def spawn(*_command: str, **_kwargs: Any) -> _HangingProcess:
                output.write_bytes(b"NEW")
                return process

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=lambda _request: True,
                termination_grace_seconds=0,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=output.parent,
                config_path=root / "config.json",
                log_path=root / "inference.log",
                model_dir=root / "models",
                required_outputs=(output,),
            )
            real_replace = os.replace

            def fail_cancel_restore(source: Any, destination: Any) -> None:
                if (
                    ".inference-backup-" in Path(source).name
                    and Path(destination) == output
                ):
                    raise OSError(f"restore failed at {root} with {secret}")
                real_replace(source, destination)

            with mock.patch(
                "backend.inference_backend.os.replace",
                side_effect=fail_cancel_restore,
            ):
                task = asyncio.create_task(backend.run(request))
                await process.waiting.wait()
                task.cancel()

                with self.assertRaises(InferenceRuntimeError) as raised:
                    await task

            error = raised.exception
            rendered_chain = "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            )
            backups = tuple(output.parent.glob(".page.png.inference-backup-*"))
            self.assertEqual(error.category, "runtime_unavailable")
            self.assertEqual(process.actions, ["terminate", "kill"])
            self.assertFalse(output.exists())
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b"OLD")
            self.assertNotIn(secret, str(error))
            self.assertNotIn(str(root), repr(error))
            self.assertNotIn(secret, rendered_chain)
            self.assertNotIn(str(root), rendered_chain)

    async def test_log_open_failure_is_runtime_error_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = "open-secret"
            log_directory = root / secret
            log_directory.mkdir()
            backend = UpstreamInferenceBackend(
                base_dir=root,
                runtime_preparer=lambda _request: True,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=root / "output",
                config_path=root / "config.json",
                log_path=log_directory,
                model_dir=root / "models",
            )

            with self.assertRaises(InferenceRuntimeError) as raised:
                await backend.run(request)

            self.assertNotIn(secret, str(raised.exception))
            self.assertNotIn(str(root), repr(raised.exception))

    async def test_spawn_failure_is_upstream_launch_error_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = "spawn-secret"

            async def spawn(*_command: str, **_kwargs: Any) -> _CompletedProcess:
                raise OSError(f"could not spawn at {root} with {secret}")

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=lambda _request: True,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=root / "output",
                config_path=root / "config.json",
                log_path=root / "inference.log",
                model_dir=root / "models",
            )

            with self.assertRaises(InferenceUpstreamError) as raised:
                await backend.run(request)

            self.assertEqual(raised.exception.classification, "launch")
            self.assertNotIn(secret, str(raised.exception))
            self.assertNotIn(str(root), repr(raised.exception))

    async def test_wait_failure_cleans_process_and_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = "wait-secret"
            process = _FailingWaitProcess(OSError(f"failed at {root} with {secret}"))

            async def spawn(*_command: str, **_kwargs: Any) -> _FailingWaitProcess:
                return process

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=lambda _request: True,
                termination_grace_seconds=0,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=root / "output",
                config_path=root / "config.json",
                log_path=root / "inference.log",
                model_dir=root / "models",
            )

            with self.assertRaises(InferenceUpstreamError) as raised:
                await backend.run(request)

            self.assertEqual(raised.exception.classification, "process_wait")
            self.assertEqual(process.actions, ["terminate", "kill"])
            self.assertNotIn(secret, str(raised.exception))
            self.assertNotIn(str(root), repr(raised.exception))

    async def test_classified_spawn_error_retains_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = InferenceMalformedOutputError("classified")

            async def spawn(*_command: str, **_kwargs: Any) -> _CompletedProcess:
                raise expected

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=lambda _request: True,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=root / "output",
                config_path=root / "config.json",
                log_path=root / "inference.log",
                model_dir=root / "models",
            )

            with self.assertRaises(InferenceMalformedOutputError) as raised:
                await backend.run(request)

            self.assertIs(raised.exception, expected)

    async def test_restore_failure_is_reported_and_keeps_recovery_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = "restore-secret"
            output = root / "output" / "page.png"
            output.parent.mkdir()
            output.write_bytes(b"OLD")

            async def spawn(*_command: str, **_kwargs: Any) -> _CompletedProcess:
                output.write_bytes(b"NEW")
                return _CompletedProcess(returncode=1)

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=lambda _request: True,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=output.parent,
                config_path=root / "config.json",
                log_path=root / "inference.log",
                model_dir=root / "models",
                required_outputs=(output,),
            )
            real_replace = os.replace

            def fail_restore(source: Any, destination: Any) -> None:
                if ".inference-backup-" in Path(source).name:
                    raise OSError(f"restore failed at {root} with {secret}")
                real_replace(source, destination)

            with mock.patch("backend.inference_backend.os.replace", side_effect=fail_restore):
                with self.assertRaises(InferenceRuntimeError) as raised:
                    await backend.run(request)

            backups = tuple(output.parent.glob(".page.png.inference-backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), b"OLD")
            self.assertNotIn(secret, str(raised.exception))
            self.assertNotIn(str(root), repr(raised.exception))

    async def test_backup_discard_failure_cannot_report_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = "discard-secret"
            output = root / "output" / "page.png"
            output.parent.mkdir()
            output.write_bytes(b"OLD")

            async def spawn(*_command: str, **_kwargs: Any) -> _CompletedProcess:
                output.write_bytes(b"NEW")
                return _CompletedProcess()

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=lambda _request: True,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=output.parent,
                config_path=root / "config.json",
                log_path=root / "inference.log",
                model_dir=root / "models",
                required_outputs=(output,),
            )

            with mock.patch.object(
                Path,
                "unlink",
                side_effect=OSError(f"discard failed at {root} with {secret}"),
            ):
                with self.assertRaises(InferenceRuntimeError) as raised:
                    await backend.run(request)

            self.assertEqual(output.read_bytes(), b"NEW")
            self.assertEqual(len(tuple(output.parent.glob(".page.png.inference-backup-*"))), 1)
            self.assertNotIn(secret, str(raised.exception))
            self.assertNotIn(str(root), repr(raised.exception))

    async def test_runtime_preparation_is_reused_only_for_the_same_cache_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepared_keys: list[str] = []

            async def spawn(*_command: str, **_kwargs: Any) -> _CompletedProcess:
                return _CompletedProcess()

            def prepare(request: InferenceRequest) -> bool:
                prepared_keys.append(request.runtime_key)
                return True

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=prepare,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=root / "output",
                config_path=root / "config.json",
                log_path=root / "inference.log",
                model_dir=root / "models",
                runtime_key="ocr-default",
            )

            first = await backend.run(request)
            second = await backend.run(request)
            third = await backend.run(replace(request, runtime_key="ocr-alternate"))

            self.assertFalse(first.runtime_reused)
            self.assertTrue(second.runtime_reused)
            self.assertFalse(third.runtime_reused)
            self.assertEqual(prepared_keys, ["ocr-default", "ocr-alternate"])

    async def test_runtime_preparation_failure_is_classified_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = "runtime-secret"

            def fail_prepare(_request: InferenceRequest) -> bool:
                raise OSError(f"failed at {root} with {secret}")

            backend = UpstreamInferenceBackend(
                base_dir=root,
                runtime_preparer=fail_prepare,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=root / "output",
                config_path=root / "config.json",
                log_path=root / "inference.log",
                model_dir=root / "models",
            )

            with self.assertRaises(InferenceRuntimeError) as raised:
                await backend.run(request)

            self.assertEqual(raised.exception.category, "runtime_unavailable")
            self.assertNotIn(secret, str(raised.exception))
            self.assertNotIn(str(root), str(raised.exception))

    def test_runtime_cache_is_shared_across_thread_event_loops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prepare_started = threading.Event()
            second_started = threading.Event()
            release_prepare = threading.Event()
            prepare_count = 0
            prepare_lock = threading.Lock()
            results: list[InferenceResult] = []
            failures: list[BaseException] = []

            async def spawn(*_command: str, **_kwargs: Any) -> _CompletedProcess:
                return _CompletedProcess()

            def prepare(_request: InferenceRequest) -> bool:
                nonlocal prepare_count
                with prepare_lock:
                    prepare_count += 1
                prepare_started.set()
                if not release_prepare.wait(timeout=5):
                    raise AssertionError("runtime preparation was not released")
                return True

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=prepare,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=root / "output",
                config_path=root / "config.json",
                log_path=root / "first.log",
                model_dir=root / "models",
                runtime_key="shared",
            )

            def run_request(run_request: InferenceRequest, started: threading.Event | None = None) -> None:
                async def invoke() -> InferenceResult:
                    if started is None:
                        return await backend.run(run_request)
                    task = asyncio.create_task(backend.run(run_request))
                    scheduled = asyncio.get_running_loop().create_future()
                    asyncio.get_running_loop().call_soon(scheduled.set_result, None)
                    await scheduled
                    started.set()
                    return await task

                try:
                    results.append(asyncio.run(invoke()))
                except BaseException as exc:
                    failures.append(exc)

            first_thread = threading.Thread(target=run_request, args=(request,))
            second_thread = threading.Thread(
                target=run_request,
                args=(replace(request, log_path=root / "second.log"), second_started),
            )
            first_thread.start()
            self.assertTrue(prepare_started.wait(timeout=5))
            second_thread.start()
            self.assertTrue(second_started.wait(timeout=5))
            release_prepare.set()
            first_thread.join(timeout=5)
            second_thread.join(timeout=5)

            self.assertFalse(first_thread.is_alive())
            self.assertFalse(second_thread.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(prepare_count, 1)
            self.assertEqual(sorted(result.runtime_reused for result in results), [False, True])

    def test_cross_loop_runtime_owner_failure_wakes_waiter_and_allows_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            owner_preparing = threading.Event()
            waiter_started = threading.Event()
            release_owner = threading.Event()
            attempts = 0
            failures: list[BaseException] = []

            async def spawn(*_command: str, **_kwargs: Any) -> _CompletedProcess:
                return _CompletedProcess()

            def prepare(_request: InferenceRequest) -> bool:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    owner_preparing.set()
                    if not release_owner.wait(timeout=5):
                        raise AssertionError("failed owner was not released")
                    raise OSError("secret /absolute/runtime/path")
                return True

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=prepare,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=root / "output",
                config_path=root / "config.json",
                log_path=root / "owner.log",
                model_dir=root / "models",
                runtime_key="retry-after-failure",
            )

            def run_request(run_request: InferenceRequest, started: threading.Event | None = None) -> None:
                async def invoke() -> InferenceResult:
                    if started is None:
                        return await backend.run(run_request)
                    task = asyncio.create_task(backend.run(run_request))
                    scheduled = asyncio.get_running_loop().create_future()
                    asyncio.get_running_loop().call_soon(scheduled.set_result, None)
                    await scheduled
                    started.set()
                    return await task

                try:
                    asyncio.run(invoke())
                except BaseException as exc:
                    failures.append(exc)

            owner = threading.Thread(target=run_request, args=(request,))
            waiter = threading.Thread(
                target=run_request,
                args=(replace(request, log_path=root / "waiter.log"), waiter_started),
            )
            owner.start()
            self.assertTrue(owner_preparing.wait(timeout=5))
            waiter.start()
            self.assertTrue(waiter_started.wait(timeout=5))
            release_owner.set()
            owner.join(timeout=5)
            waiter.join(timeout=5)

            self.assertEqual(len(failures), 2)
            self.assertTrue(all(isinstance(exc, InferenceRuntimeError) for exc in failures))
            retry = asyncio.run(backend.run(replace(request, log_path=root / "retry.log")))
            self.assertFalse(retry.runtime_reused)
            self.assertEqual(attempts, 2)

    def test_cross_loop_runtime_owner_cancellation_wakes_waiter_and_allows_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            owner_preparing = threading.Event()
            waiter_started = threading.Event()
            owner_task_ready = threading.Event()
            retry_enabled = threading.Event()
            attempts = 0
            failures: list[BaseException] = []
            owner_loop: list[asyncio.AbstractEventLoop] = []
            owner_task: list[asyncio.Task[InferenceResult]] = []

            async def spawn(*_command: str, **_kwargs: Any) -> _CompletedProcess:
                return _CompletedProcess()

            async def prepare(_request: InferenceRequest) -> bool:
                nonlocal attempts
                attempts += 1
                if not retry_enabled.is_set():
                    owner_preparing.set()
                    await asyncio.Future()
                return True

            backend = UpstreamInferenceBackend(
                base_dir=root,
                process_factory=spawn,
                runtime_preparer=prepare,
            )
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=root / "output",
                config_path=root / "config.json",
                log_path=root / "owner.log",
                model_dir=root / "models",
                runtime_key="retry-after-cancel",
            )

            def run_owner() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                task = loop.create_task(backend.run(request))
                owner_loop.append(loop)
                owner_task.append(task)
                owner_task_ready.set()
                try:
                    loop.run_until_complete(task)
                except BaseException as exc:
                    failures.append(exc)
                finally:
                    loop.close()

            def run_waiter() -> None:
                async def invoke() -> InferenceResult:
                    task = asyncio.create_task(
                        backend.run(replace(request, log_path=root / "waiter.log"))
                    )
                    scheduled = asyncio.get_running_loop().create_future()
                    asyncio.get_running_loop().call_soon(scheduled.set_result, None)
                    await scheduled
                    waiter_started.set()
                    return await task

                try:
                    asyncio.run(invoke())
                except BaseException as exc:
                    failures.append(exc)

            owner = threading.Thread(target=run_owner)
            waiter = threading.Thread(target=run_waiter)
            owner.start()
            self.assertTrue(owner_task_ready.wait(timeout=5))
            self.assertTrue(owner_preparing.wait(timeout=5))
            waiter.start()
            self.assertTrue(waiter_started.wait(timeout=5))
            retry_enabled.set()
            owner_loop[0].call_soon_threadsafe(owner_task[0].cancel)
            owner.join(timeout=5)
            waiter.join(timeout=5)

            self.assertEqual(len(failures), 2)
            self.assertTrue(any(isinstance(exc, asyncio.CancelledError) for exc in failures))
            self.assertTrue(any(isinstance(exc, InferenceRuntimeError) for exc in failures))
            retry = asyncio.run(backend.run(replace(request, log_path=root / "retry.log")))
            self.assertFalse(retry.runtime_reused)
            self.assertEqual(attempts, 2)

    async def test_detect_text_mask_adapts_default_detector_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = UpstreamInferenceBackend(root)
            backend._ensure_vendor_import_path = mock.Mock()  # type: ignore[method-assign]
            backend.prepare_runtime_patches = mock.Mock(return_value=True)  # type: ignore[method-assign]
            source = np.full((48, 64, 3), 240, dtype=np.uint8)
            raw_mask = np.zeros((48, 64), dtype=np.uint8)
            raw_mask[12:20, 18:30] = 255
            dispatch_arguments: list[tuple[Any, ...]] = []

            async def fake_dispatch(*args: Any) -> tuple[list[Any], np.ndarray, np.ndarray]:
                dispatch_arguments.append(args)
                textline = SimpleNamespace(
                    pts=np.array([[16, 10], [32, 10], [32, 22], [16, 22]]),
                    prob=0.93,
                )
                return [textline], raw_mask, raw_mask.copy()

            fake_package = ModuleType("manga_translator")
            fake_package.__path__ = []  # type: ignore[attr-defined]
            fake_config = ModuleType("manga_translator.config")
            fake_config.Detector = SimpleNamespace(default="default")
            fake_detection = ModuleType("manga_translator.detection")
            fake_detection.dispatch = fake_dispatch
            fake_utils = ModuleType("manga_translator.utils")

            class FakeModelWrapper:
                _MODEL_DIR = ""

            fake_utils.ModelWrapper = FakeModelWrapper
            fake_modules = {
                "manga_translator": fake_package,
                "manga_translator.config": fake_config,
                "manga_translator.detection": fake_detection,
                "manga_translator.utils": fake_utils,
            }
            with mock.patch.dict(sys.modules, fake_modules):
                result = await backend.detect_text_mask(
                    source,
                    model_dir=root / "models",
                    device="cuda",
                    detection_size=2048,
                )

            self.assertEqual(FakeModelWrapper._MODEL_DIR, str(root / "models"))
            self.assertEqual(len(dispatch_arguments), 1)
            self.assertEqual(dispatch_arguments[0][0], "default")
            self.assertIs(dispatch_arguments[0][1], source)
            self.assertEqual(dispatch_arguments[0][2], 2048)
            self.assertEqual(dispatch_arguments[0][10], "cuda")
            self.assertTrue(np.array_equal(result["mask"], raw_mask))
            self.assertEqual(result["textlines"][0]["probability"], 0.93)
            self.assertEqual(
                result["textlines"][0]["points"],
                [[16, 10], [32, 10], [32, 22], [16, 22]],
            )

    async def test_deterministic_fake_uses_the_same_request_result_interface(self) -> None:
        class FakeInferenceBackend:
            def __init__(self) -> None:
                self.runtime_preparations = 0

            async def run(self, request: InferenceRequest) -> InferenceResult:
                for output in request.required_outputs:
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(b"fake")
                return InferenceResult(outputs=request.required_outputs, exit_code=0)

            async def erase_selection(
                self,
                base_image: Any,
                _selection_mask: Any,
                **_kwargs: Any,
            ) -> Any:
                return base_image

            async def detect_text_mask(
                self,
                source_image: Any,
                **_kwargs: Any,
            ) -> dict[str, Any]:
                return {"mask": source_image[..., 0] * 0, "textlines": []}

            async def recognize_region(
                self,
                _source_image: Any,
                _bbox: list[int],
                **_kwargs: Any,
            ) -> dict[str, Any]:
                return {"source_text": "fake"}

            async def translate_texts(
                self,
                texts: list[str],
                **_kwargs: Any,
            ) -> tuple[str, ...]:
                return tuple(f"translated:{text}" for text in texts)

            def create_text_region(self, **attributes: Any) -> Any:
                return SimpleNamespace(**attributes)

            def build_command(self, request: InferenceRequest) -> tuple[str, ...]:
                command = [
                    "fake-python",
                    "-m",
                    "fake-inference",
                    "local",
                    "--model-dir",
                    str(request.model_dir),
                ]
                if request.use_gpu:
                    command.append("--use-gpu")
                return tuple(command)

            def prepare_runtime_patches(self) -> bool:
                self.runtime_preparations += 1
                return True

            def model_download_notice(self, _log_path: Path) -> str:
                return "fake model notice"

            def runtime_contract_notice(self, _log_path: Path) -> str:
                return "fake runtime notice"

            def format_failure(self, _log_path: Path, _stage_label: str = "") -> str:
                return "fake failure"

            def pre_render_failure(self, _log_path: Path, stage_label: str) -> str:
                return f"fake pre-render: {stage_label}"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "output" / "page.png"
            request = InferenceRequest(
                source_dir=root / "source",
                output_dir=output.parent,
                config_path=root / "config.json",
                log_path=root / "inference.log",
                model_dir=root / "models",
                required_outputs=(output,),
            )
            backend: InferenceBackend = FakeInferenceBackend()

            result = await backend.run(request)

            self.assertIsInstance(backend, InferenceBackend)
            self.assertEqual(result.outputs, (output,))

            engine = TranslatorEngine(
                BACKEND_DIR,
                app_paths=_test_paths(root),
                inference_backend=backend,
            )
            contract = engine.build_inference_runtime_contract({"use_gpu": True})
            engine._ensure_runtime_patches()
            self.assertEqual(contract["status"], "ready")
            self.assertEqual(contract["model_dir"], str(root / "models"))
            self.assertEqual(engine._model_download_notice(root / "unused.log"), "fake model notice")
            self.assertEqual(engine._runtime_contract_notice(root / "unused.log"), "fake runtime notice")
            self.assertEqual(engine._format_failure(root / "unused.log"), "fake failure")
            self.assertEqual(
                engine._detect_pre_render_failure(root / "unused.log", "recognition"),
                "fake pre-render: recognition",
            )
            self.assertEqual(backend.runtime_preparations, 1)

            translator_source = (BACKEND_DIR / "engine" / "translator.py").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("getattr(self.inference_backend", translator_source)


if __name__ == "__main__":
    unittest.main()
