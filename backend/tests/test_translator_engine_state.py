from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
import zipfile
from argparse import Namespace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib import error as urllib_error

import cv2
import numpy as np
from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import engine.translator as translator_module
from domain.project_artifacts import PageArtifactEvent, ProjectArtifactState
from engine.image_cleanup import SeedreamImageCleanupClient
from engine.project_workspace import CorruptProjectArtifactError
from engine.translator import InvalidStorageIdentifierError, TranslatorEngine
from runtime_paths import AppPaths
from workflow_coordinator import TranslatorEngineWorkflowAdapter, WorkflowCoordinator
from workflow_events import ProjectCommand


def make_test_paths(root: Path) -> AppPaths:
    return AppPaths(
        code_dir=BACKEND_DIR,
        app_data_dir=root / "app-data",
        models_dir=root / "models",
        output_dir=root / "output",
        logs_dir=root / "logs",
        cache_dir=root / "cache",
        config_dir=root / "config",
    )


class TranslatorEngineStateTests(unittest.TestCase):
    def make_engine(self, root: Path) -> TranslatorEngine:
        return TranslatorEngine(BACKEND_DIR, app_paths=make_test_paths(root))

    def make_workflow_coordinator(
        self,
        engine: TranslatorEngine,
        session: dict[str, object],
    ) -> WorkflowCoordinator:
        adapter = TranslatorEngineWorkflowAdapter(engine)
        return WorkflowCoordinator(
            project_loader=lambda _project_id: session,
            project_view_builder=engine.build_client_session_payload,
            project_workspace=engine.project_workspace,
            preparation_adapter=adapter,
        )

    def test_snapshot_pin_and_retention_share_one_project_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            workspace = engine.project_workspace
            project_id = "atomic-snapshot-pin"
            snapshots_dir = workspace.project_snapshots_dir(project_id)
            snapshots_dir.mkdir(parents=True)
            for index in range(21):
                snapshot_id = f"snapshot-{index:02d}"
                workspace.write_json_file(
                    snapshots_dir / f"{snapshot_id}.json",
                    {
                        "snapshot_id": snapshot_id,
                        "created_at": f"2026-07-01T00:{index:02d}:00+00:00",
                        "kind": "automatic",
                        "pinned": False,
                    },
                )

            target_snapshot_id = "snapshot-00"
            target_path = snapshots_dir / f"{target_snapshot_id}.json"
            pin_write_reached = threading.Event()
            pin_write_allowed = threading.Event()
            pin_write_completed = threading.Event()
            retention_lock_probed = threading.Event()
            retention_blocked_on_pin = threading.Event()
            retention_acquired_without_waiting = threading.Event()
            retention_catalog_read = threading.Event()
            thread_errors: list[BaseException] = []
            pin_results: list[list[dict[str, object]]] = []
            original_write_json = workspace.write_json_file
            original_read_manifests = workspace.read_snapshot_manifests
            project_lock = workspace._head_commit_lock(project_id)

            class RetentionLockProbe:
                def __enter__(self):
                    acquired = project_lock.acquire(blocking=False)
                    if acquired:
                        retention_acquired_without_waiting.set()
                        retention_lock_probed.set()
                    else:
                        retention_blocked_on_pin.set()
                        retention_lock_probed.set()
                        pin_write_allowed.set()
                        project_lock.acquire()
                    return project_lock

                def __exit__(self, exc_type, exc_value, traceback):
                    project_lock.release()
                    return False

            def observe_project_lock(requested_project_id: str):
                if threading.current_thread().name == "snapshot-retention":
                    return RetentionLockProbe()
                return project_lock

            def pause_pin_write(path: Path, payload: object) -> None:
                if (
                    threading.current_thread().name == "snapshot-pin"
                    and Path(path) == target_path
                ):
                    pin_write_reached.set()
                    if not retention_lock_probed.wait(timeout=5):
                        raise RuntimeError("retention did not attempt the project lock")
                    if not pin_write_allowed.wait(timeout=5):
                        raise RuntimeError("pin write was not released")
                    try:
                        original_write_json(path, payload)
                    finally:
                        pin_write_completed.set()
                    return
                original_write_json(path, payload)

            def pause_retention_after_catalog_read(
                requested_project_id: str,
            ) -> list[dict[str, object]]:
                manifests = original_read_manifests(requested_project_id)
                if threading.current_thread().name == "snapshot-retention":
                    retention_catalog_read.set()
                    pin_write_allowed.set()
                    if not pin_write_completed.wait(timeout=5):
                        raise RuntimeError("pin write did not complete")
                return manifests

            def pin_snapshot() -> None:
                try:
                    pin_results.append(
                        engine.set_snapshot_pinned(
                            project_id,
                            target_snapshot_id,
                            True,
                        )
                    )
                except BaseException as exc:
                    thread_errors.append(exc)

            def enforce_retention() -> None:
                try:
                    workspace.enforce_snapshot_retention(project_id)
                except BaseException as exc:
                    thread_errors.append(exc)

            self.addCleanup(pin_write_allowed.set)
            self.addCleanup(pin_write_completed.set)
            with (
                mock.patch.object(
                    workspace,
                    "write_json_file",
                    side_effect=pause_pin_write,
                ),
                mock.patch.object(
                    workspace,
                    "read_snapshot_manifests",
                    side_effect=pause_retention_after_catalog_read,
                ),
                mock.patch.object(
                    workspace,
                    "_head_commit_lock",
                    side_effect=observe_project_lock,
                ),
            ):
                pin_thread = threading.Thread(
                    target=pin_snapshot,
                    name="snapshot-pin",
                    daemon=True,
                )
                pin_thread.start()
                self.assertTrue(pin_write_reached.wait(timeout=5))

                retention_thread = threading.Thread(
                    target=enforce_retention,
                    name="snapshot-retention",
                    daemon=True,
                )
                retention_thread.start()
                self.assertTrue(retention_lock_probed.wait(timeout=5))

                pin_thread.join(timeout=5)
                retention_thread.join(timeout=5)
                self.assertFalse(pin_thread.is_alive())
                self.assertFalse(retention_thread.is_alive())

            self.assertEqual(thread_errors, [])
            self.assertTrue(retention_blocked_on_pin.is_set())
            self.assertFalse(retention_acquired_without_waiting.is_set())
            self.assertTrue(retention_catalog_read.is_set())
            self.assertEqual(len(pin_results), 1)
            returned_target = next(
                (
                    item
                    for item in pin_results[0]
                    if item["snapshot_id"] == target_snapshot_id
                ),
                None,
            )
            self.assertIsNotNone(returned_target)
            self.assertTrue(returned_target["pinned"])
            authoritative_target = next(
                (
                    item
                    for item in workspace.read_snapshot_manifests(project_id)
                    if item["snapshot_id"] == target_snapshot_id
                ),
                None,
            )
            self.assertIsNotNone(authoritative_target)
            self.assertTrue(authoritative_target["pinned"])

    def test_project_command_fingerprint_uses_canonical_action_normalized_config_and_scope(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            implicit_defaults = engine.project_command_fingerprint(
                action="resume-translate",
                raw_config={},
                target_stored_name=None,
            )
            explicit_defaults = engine.project_command_fingerprint(
                action="resume-translate",
                raw_config={"target_lang": "CHS"},
                target_stored_name=None,
            )

            self.assertEqual(implicit_defaults, explicit_defaults)
            self.assertNotEqual(
                implicit_defaults,
                engine.project_command_fingerprint(
                    action="translate",
                    raw_config={},
                    target_stored_name=None,
                ),
            )
            self.assertNotEqual(
                implicit_defaults,
                engine.project_command_fingerprint(
                    action="resume-translate",
                    raw_config={},
                    target_stored_name="page-1.png",
                ),
            )

    def test_private_translation_composition_separates_phase_checkpoints(
        self,
    ) -> None:
        cases = (
            (
                "fresh",
                False,
                "idle",
                {},
                True,
                {"page-1.png": "detected", "page-2.png": "detected"},
                False,
            ),
            (
                "restored-detected",
                True,
                "detected",
                {"page-1.png": "detected", "page-2.png": "detected"},
                False,
                {"page-1.png": "detected", "page-2.png": "detected"},
                False,
            ),
            (
                "restored-translating",
                True,
                "translating",
                {"page-1.png": "rendered"},
                False,
                {"page-1.png": "rendered"},
                True,
            ),
        )
        for (
            label,
            pending_restored,
            workflow_stage,
            initial_completed,
            expected_detect_called,
            expected_translation_completed,
            expected_skip_completed,
        ) in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                engine = self.make_engine(Path(tmp))
                session = {
                    "translated_dir": str(Path(tmp) / "translated"),
                    "workflow_stage": workflow_stage,
                    "last_config": {},
                }
                page_checkpoints = dict(initial_completed)
                observed: dict[str, object] = {"detect_called": False}

                async def fake_detection(**kwargs):
                    observed["detect_called"] = True
                    kwargs["page_checkpoints"].update(
                        {"page-1.png": "detected", "page-2.png": "detected"}
                    )
                    session["workflow_stage"] = "detected"
                    return {"workflow_stage": "detected"}

                async def fake_translation_resume(**kwargs):
                    observed["translation_completed"] = dict(
                        kwargs["page_checkpoints"]
                    )
                    observed["skip_completed"] = kwargs["skip_completed"]
                    return {"workflow_stage": "translated"}

                with mock.patch.object(
                    engine,
                    "_detect_session",
                    side_effect=fake_detection,
                ), mock.patch.object(
                    engine,
                    "_resume_translation_session",
                    side_effect=fake_translation_resume,
                ):
                    asyncio.run(
                        engine._translate_session(
                            session_id="project-a",
                            session=session,
                            raw_config={},
                            progress_callback=lambda _event: None,
                            persist=False,
                            pending_restored=pending_restored,
                            page_checkpoints=page_checkpoints,
                        )
                    )

                self.assertIs(
                    observed["detect_called"],
                    expected_detect_called,
                )
                self.assertEqual(
                    observed["translation_completed"],
                    expected_translation_completed,
                )
                self.assertIs(
                    observed["skip_completed"],
                    expected_skip_completed,
                )

    def make_recognized_zero_region_project(
        self,
        root: Path,
        *,
        project_id: str,
        page_ids: list[str],
    ) -> tuple[TranslatorEngine, dict[str, object]]:
        engine = self.make_engine(root)
        source_dir = engine.project_workspace.project_source_dir(project_id)
        output_dir = engine.project_workspace.project_translated_dir(project_id)
        cache_dir = root / "cache"
        source_dir.mkdir(parents=True)
        output_dir.mkdir(parents=True)
        artifact_state = ProjectArtifactState.create(page_ids)
        for index, page_id in enumerate(page_ids, start=1):
            Image.new("RGB", (16, 16), (255 - index, 255, 255)).save(
                source_dir / page_id
            )
            page_cache_dir = cache_dir / page_id
            page_cache_dir.mkdir(parents=True)
            Image.new("RGB", (16, 16), (255, 255 - index, 255)).save(
                page_cache_dir / "inpainted.png"
            )
            (page_cache_dir / "regions.json").write_text("[]", encoding="utf-8")
            (page_cache_dir / "meta.json").write_text(
                json.dumps({"base_kind": "inpainted"}),
                encoding="utf-8",
            )
            engine.project_workspace.write_json_file(
                engine.project_workspace.project_page_document_path(
                    project_id,
                    page_id,
                ),
                {
                    "page_id": page_id,
                    "dimensions": {"width": 16, "height": 16},
                    "regions": [],
                    "metadata": {"revision": 1},
                },
            )
            artifact_state = artifact_state.apply(
                page_id,
                PageArtifactEvent.RECOGNIZED,
            )
        session: dict[str, object] = {
            "source_dir": str(source_dir),
            "translated_dir": str(output_dir),
            "source_images": [
                {"name": page_id, "stored_name": page_id}
                for page_id in page_ids
            ],
            "translated_output_map": {},
            "download_path": "",
            "workflow_stage": "detected",
            "rerender_cache_dir": str(cache_dir),
            "manual_regions": {},
            "last_config": {},
            "project_glossary": {
                "entries": [],
                "auto_extract_completed": True,
            },
            "translation_region_overrides": {},
            "translation_region_skip_overrides": {},
            "translation_region_disabled_overrides": {},
            "translation_region_layout_overrides": {},
            "style_region_overrides": {},
            "artifact_state": artifact_state.model_dump(mode="json"),
        }

        async def render_zero_region_page(*args, **kwargs) -> None:
            source_path = Path(kwargs.get("source_path") or args[0])
            output_path = Path(kwargs.get("output_path") or args[1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(source_path) as source_image:
                source_image.convert("RGB").save(output_path)

        engine._render_cached_page = render_zero_region_page  # type: ignore[method-assign]
        engine.initialize_project(project_id, session, title="Zero regions")
        return engine, session

    def interrupt_translation_after_first_verified_page(
        self,
        engine: TranslatorEngine,
        *,
        project_id: str,
        session: dict[str, object],
        config: dict[str, object],
    ) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        render_count = 0
        original_render = engine._render_cached_page

        async def collect_progress(event: dict[str, object]) -> None:
            events.append(event)

        async def interrupt_second_render(*args, **kwargs):
            nonlocal render_count
            render_count += 1
            if render_count == 2:
                raise RuntimeError("interrupt after first verified page")
            return await original_render(*args, **kwargs)

        with mock.patch.object(
            engine,
            "_render_cached_page",
            side_effect=interrupt_second_render,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "interrupt after first verified page",
            ):
                asyncio.run(
                    self.make_workflow_coordinator(engine, session).execute(
                        ProjectCommand(
                            project_id=project_id,
                            action="resume-translate",
                            config=config,
                        ),
                        progress=collect_progress,
                    )
                )
        return events

    def load_patched_text_mask_utils(self):
        vendor_root = BACKEND_DIR / "manga-image-translator" / "manga_translator"
        if not vendor_root.exists():
            self.skipTest("manga-image-translator vendor checkout is not installed")

        sys.path.insert(0, str(BACKEND_DIR / "manga-image-translator"))
        spec = importlib.util.spec_from_file_location(
            "manga_translator.mask_refinement.patched_text_mask_utils_test",
            BACKEND_DIR / "patched_text_mask_utils.py",
        )
        module = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        spec.loader.exec_module(module)
        return module

    def load_patched_rendering(self):
        vendor_root = BACKEND_DIR / "manga-image-translator" / "manga_translator"
        if not vendor_root.exists():
            self.skipTest("manga-image-translator vendor checkout is not installed")

        sys.path.insert(0, str(BACKEND_DIR / "manga-image-translator"))
        sys.path.insert(0, str(BACKEND_DIR))
        from patch_pydensecrf import patch_mask_refinement

        self.assertTrue(patch_mask_refinement())
        sys.modules.pop("manga_translator.rendering", None)
        import manga_translator.rendering as rendering

        return rendering

    def test_engine_does_not_own_project_busy_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            for name in (
                "active_sessions",
                "active_sessions_lock",
                "project_command_locks",
                "try_mark_session_busy",
                "mark_session_busy",
                "clear_session_busy",
                "is_session_busy",
                "get_session_busy_action",
            ):
                with self.subTest(name=name):
                    self.assertFalse(hasattr(engine, name))

    def test_engine_command_places_general_options_after_local_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            config = engine._normalize_config({"use_gpu": True})
            (root / "source").mkdir()
            command = engine._build_command(
                root / "source",
                root / "output",
                root / "detect.json",
                config,
                prep_manual=True,
            )

            local_index = command.index("local")
            self.assertGreater(command.index("--use-gpu"), local_index)
            self.assertGreater(command.index("--model-dir"), local_index)
            self.assertEqual(
                command[command.index("--model-dir") + 1],
                str(engine.model_dir),
            )

    def test_engine_command_survives_upstream_parser(self) -> None:
        vendor_package = (
            BACKEND_DIR
            / "manga-image-translator"
            / "manga_translator"
            / "args.py"
        )
        if not vendor_package.exists():
            self.skipTest("manga-image-translator vendor checkout is not installed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            config = engine._normalize_config({"use_gpu": True})
            (root / "source").mkdir()
            command = engine._build_command(
                root / "source",
                root / "output",
                root / "detect.json",
                config,
                prep_manual=True,
            )

            engine._ensure_vendor_import_path()
            from manga_translator.args import parser, reparse

            parsed, unknown = parser.parse_known_args(command[3:])
            effective = Namespace(**{**vars(parsed), **vars(reparse(unknown))})
            self.assertTrue(effective.use_gpu)
            self.assertEqual(effective.model_dir, str(engine.model_dir))

    def test_runtime_contract_log_is_reported_as_user_facing_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            log_path = root / "detect.log"
            log_path.write_text(
                f"[RuntimeContract] device=cuda model_dir={engine.model_dir}\n",
                encoding="utf-8",
            )

            notice = engine._runtime_contract_notice(log_path)

            self.assertIn("NVIDIA CUDA", notice)
            self.assertNotIn(str(engine.model_dir), notice)

    def test_detect_profile_does_not_initialize_translation_or_inpainting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            config = engine._normalize_config(
                {
                    "translator": "gemini",
                    "api_key": "must-not-be-needed-for-detection",
                }
            )

            config_path = engine._write_config("project-a", config, profile="detect")
            payload = json.loads(config_path.read_text(encoding="utf-8"))

            self.assertEqual(payload["translator"]["translator"], "none")
            self.assertEqual(payload["inpainter"]["inpainter"], "original")
            self.assertEqual(payload["render"]["renderer"], "none")

    def test_detect_only_runtime_patch_returns_before_translation_mask_and_inpainting(self) -> None:
        runtime_path = (
            BACKEND_DIR
            / "manga-image-translator"
            / "manga_translator"
            / "manga_translator.py"
        )
        if not runtime_path.exists():
            self.skipTest("manga-image-translator vendor checkout is not installed")

        content = runtime_path.read_text(encoding="utf-8")
        early_return = content.index("if self.prep_manual:", content.index("# Apply pre-dictionary"))
        translation_stage = content.index("# -- Translation", early_return)
        mask_stage = content.index("# -- Mask refinement", translation_stage)

        self.assertLess(early_return, translation_stage)
        self.assertLess(translation_stage, mask_stage)
        preload_block = content[
            content.index("# Solar-Manga-Translator: detection")
            : content.index("# translate", content.index("# Solar-Manga-Translator: detection"))
        ]
        self.assertIn("if not self.prep_manual:", preload_block)
        self.assertIn("await prepare_inpainting", preload_block)
        self.assertIn("await prepare_translation", preload_block)
        self.assertIn("MT_DISABLE_INTERNAL_LOG_FILE", content)

    def test_engine_environment_routes_logs_to_application_log_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            env = engine._build_env(engine._normalize_config({}))

            self.assertEqual(env["MT_DISABLE_INTERNAL_LOG_FILE"], "1")

    def test_failed_detect_restores_previous_outputs_cache_and_session_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-a"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            Image.new("RGB", (16, 16), (1, 2, 3)).save(output_dir / "page-1.png")
            cache_dir = engine._prepare_rerender_cache_dir(project_id, reset=True)
            page_cache_dir = cache_dir / "page-1.png"
            page_cache_dir.mkdir()
            (page_cache_dir / "regions.json").write_text("[]", encoding="utf-8")
            Image.new("RGB", (16, 16), (4, 5, 6)).save(page_cache_dir / "inpainted.png")
            existing_archive = root / "existing.zip"
            existing_archive.write_bytes(b"existing")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "download_path": str(existing_archive),
                "translated_output_map": {"page-1.png": "page-1.png"},
                "workflow_stage": "translated",
                "rerender_cache_dir": str(cache_dir),
                "manual_regions": {},
            }
            engine.initialize_project(project_id, session, title="Existing project")
            persisted_state_path = engine._project_session_state_path(project_id)
            persisted_state_before = persisted_state_path.read_bytes()

            async def fail_command(**_kwargs):
                return 1

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._run_translation_command = fail_command  # type: ignore[method-assign]
            engine._format_failure = lambda _path: "synthetic failure"  # type: ignore[method-assign]

            async def progress(_event):
                return None

            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                asyncio.run(
                    self.make_workflow_coordinator(engine, session).execute(
                        ProjectCommand(
                            project_id=project_id,
                            action="detect",
                            config={"translator": "gemini", "api_key": "invalid"},
                        ),
                        progress=progress,
                    )
                )

            self.assertEqual(session["workflow_stage"], "translated")
            self.assertEqual(session["download_path"], str(existing_archive))
            self.assertEqual(session["translated_output_map"], {"page-1.png": "page-1.png"})
            self.assertEqual(
                np.asarray(Image.open(output_dir / "page-1.png"))[0, 0].tolist(),
                [1, 2, 3],
            )
            self.assertEqual(
                np.asarray(Image.open(page_cache_dir / "inpainted.png"))[0, 0].tolist(),
                [4, 5, 6],
            )
            self.assertEqual(persisted_state_path.read_bytes(), persisted_state_before)

    def test_failed_book_translation_reuses_pending_completed_pages_on_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-a"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            page_ids = ["page-1.png", "page-2.png"]
            for page_id in page_ids:
                Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / page_id)
            cache_dir = engine._prepare_rerender_cache_dir(project_id, reset=True)
            for page_id in page_ids:
                page_cache_dir = cache_dir / page_id
                page_cache_dir.mkdir()
                (page_cache_dir / "regions.json").write_text("[]", encoding="utf-8")
                (page_cache_dir / "meta.json").write_text(
                    json.dumps({"base_kind": "inpainted"}),
                    encoding="utf-8",
                )
                Image.new("RGB", (16, 16), (240, 240, 240)).save(
                    page_cache_dir / "inpainted.png"
                )
            artifact_state = ProjectArtifactState.create(page_ids)
            for page_id in page_ids:
                artifact_state = artifact_state.apply(page_id, PageArtifactEvent.RECOGNIZED)
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [
                    {"name": page_id, "stored_name": page_id}
                    for page_id in page_ids
                ],
                "translated_output_map": {},
                "workflow_stage": "detected",
                "rerender_cache_dir": str(cache_dir),
                "manual_regions": {},
                "last_config": {},
                "artifact_state": artifact_state.model_dump(mode="json"),
            }
            engine.initialize_project(project_id, session, title="Pending retry")
            initial_head = engine.project_workspace.read_project_head(project_id)
            attempt = {"number": 1}
            rendered_by_attempt: dict[int, list[str]] = {1: [], 2: [], 3: []}
            visible_output_dirs_during_work: list[str] = []

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._project_glossary_auto_extract_completed = lambda _session: True  # type: ignore[method-assign]
            engine._attach_project_glossary_context = lambda *_args: None  # type: ignore[method-assign]
            engine._ensure_editable_page_cache = lambda **_kwargs: True  # type: ignore[method-assign]
            engine._prepare_cached_regions_for_edit = lambda *_args, **_kwargs: []  # type: ignore[method-assign]
            engine._persist_translated_regions = lambda **_kwargs: None  # type: ignore[method-assign]
            engine._select_complex_repair_images = lambda *_args, **_kwargs: []  # type: ignore[method-assign]

            async def no_op_async(*_args, **_kwargs):
                return None

            async def render_page(*, output_path, **_kwargs):
                visible_output_dirs_during_work.append(str(Path(output_path).parent))
                page_id = Path(output_path).name
                rendered_by_attempt[attempt["number"]].append(page_id)
                if attempt["number"] == 1 and page_id == "page-2.png":
                    raise RuntimeError("synthetic page-2 failure")
                Image.new("RGB", (16, 16), (0, 200, 0)).save(output_path)

            engine._translate_cached_regions = no_op_async  # type: ignore[method-assign]
            engine._ensure_translation_base_image = no_op_async  # type: ignore[method-assign]
            engine._render_cached_page = render_page  # type: ignore[method-assign]
            archive_path = root / "translated.zip"
            engine.build_session_archive = lambda *_args, **_kwargs: str(archive_path)  # type: ignore[method-assign]

            async def progress(_event):
                return None

            config = {"translator": "none", "target_lang": "CHS"}
            coordinator = self.make_workflow_coordinator(engine, session)
            command = ProjectCommand(
                project_id=project_id,
                action="resume-translate",
                config=config,
            )
            with self.assertRaisesRegex(RuntimeError, "page-2 failure"):
                asyncio.run(
                    coordinator.execute(command, progress=progress)
                )

            pending = engine.project_workspace.read_pending_artifact_set(project_id)
            self.assertIsNotNone(pending)
            self.assertEqual(engine.project_workspace.read_project_head(project_id), initial_head)
            self.assertFalse((output_dir / "page-1.png").exists())
            self.assertEqual(pending["page_checkpoints"]["page-1.png"], "rendered")

            attempt["number"] = 2
            with mock.patch.object(
                engine.project_workspace,
                "commit_project_head",
                side_effect=OSError("synthetic head commit failure"),
            ):
                with self.assertRaisesRegex(OSError, "head commit failure"):
                    asyncio.run(
                        coordinator.execute(command, progress=progress)
                    )

            pending_after_commit_failure = (
                engine.project_workspace.read_pending_artifact_set(project_id)
            )
            self.assertEqual(
                pending_after_commit_failure["page_checkpoints"],
                {page_id: "finalized" for page_id in page_ids},
            )
            self.assertEqual(engine.project_workspace.read_project_head(project_id), initial_head)
            self.assertFalse((output_dir / "page-1.png").exists())
            self.assertFalse((output_dir / "page-2.png").exists())

            attempt["number"] = 3
            result = asyncio.run(
                coordinator.execute(command, progress=progress)
            )

            self.assertEqual(rendered_by_attempt[1], ["page-1.png", "page-2.png"])
            self.assertEqual(rendered_by_attempt[2], ["page-2.png"])
            self.assertEqual(rendered_by_attempt[3], [])
            self.assertTrue(visible_output_dirs_during_work)
            self.assertTrue(
                all(path != str(output_dir) for path in visible_output_dirs_during_work)
            )
            self.assertTrue((output_dir / "page-1.png").exists())
            self.assertTrue((output_dir / "page-2.png").exists())
            self.assertEqual(result["workflow_stage"], "translated")
            self.assertIsNone(engine.project_workspace.read_pending_artifact_set(project_id))
            snapshots = engine.project_workspace.read_snapshot_manifests(project_id)
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(snapshots[0]["kind"], "resume_translation")
            self.assertEqual(
                snapshots[0]["project_head_revision_id"],
                engine.project_workspace.read_project_head(project_id)[
                    "revision_id"
                ],
            )

    def test_project_command_snapshot_keeps_logical_config_without_runtime_paths_or_credentials(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = "snapshot-storage-boundary"
            engine, session = self.make_recognized_zero_region_project(
                root,
                project_id=project_id,
                page_ids=["page-1.png"],
            )
            bundled_font_dir = root / "fonts" / "system"
            custom_font_dir = root / "fonts" / "custom"
            bundled_font_dir.mkdir(parents=True)
            custom_font_dir.mkdir(parents=True)
            (bundled_font_dir / "SourceHanSansSC-Bold.otf").write_bytes(b"system-font")
            (custom_font_dir / "NotoSansSC-Bold.otf").write_bytes(b"project-font")
            engine.bundled_font_dirs = [bundled_font_dir]
            engine.custom_font_dirs = [custom_font_dir]
            config = {
                "translator": "none",
                "target_lang": "ENG",
                "api_key": "translator-secret-literal",
                "openai_base_url": "https://provider.example/v1",
                "openai_model": "logical-translation-model",
                "font_key": "project:NotoSansSC-Bold.otf",
                "style_font_keys": {
                    "gothic": "system:SourceHanSansSC-Bold.otf",
                },
                "image_cleanup_mode": "seedream-image",
                "image_cleanup_model": "doubao-seedream-4-0-250828",
                "image_cleanup_api_key": "cleanup-secret-literal",
                "advanced_erase_provider": "volcengine-ark",
                "advanced_erase_base_url": "https://erase.example/v3",
                "advanced_erase_model": "logical-erase-model",
                "advanced_erase_api_key": "erase-secret-literal",
            }
            original_normalize_config = engine._normalize_config

            def normalize_with_storage_boundary_aliases(*args, **kwargs):
                normalized = original_normalize_config(*args, **kwargs)
                normalized["provider_options"] = {
                    "accessToken": "camel-access-token-secret",
                    "client secret": "spaced-client-secret",
                    "APIKey": "acronym-api-key-secret",
                    "auth-token": "hyphen-auth-token-secret",
                    "nested": {
                        "access_token": "exact-access-token-secret",
                        "auth token": "spaced-auth-token-secret",
                        "fontPath": str(root / "private-font-path.otf"),
                        "private---dir": str(root / ".project-working-set-private"),
                    },
                    "base_url": "https://nested-provider.example/v1",
                    "font_key": "project:NotoSansSC-Bold.otf",
                    "style_font_keys": {
                        "gothic": "system:SourceHanSansSC-Bold.otf",
                    },
                }
                return normalized

            with mock.patch.object(
                engine,
                "_normalize_config",
                side_effect=normalize_with_storage_boundary_aliases,
            ):
                result = asyncio.run(
                    self.make_workflow_coordinator(engine, session).execute(
                        ProjectCommand(
                            project_id=project_id,
                            action="resume-translate",
                            config=config,
                        )
                    )
                )

            manifest_path = next(
                engine.project_workspace.project_snapshots_dir(project_id).glob(
                    "*.json"
                )
            )
            raw_manifest = manifest_path.read_bytes()
            snapshot = json.loads(raw_manifest)
            last_config = snapshot["last_config"]
            self.assertEqual(last_config["font_key"], config["font_key"])
            self.assertEqual(
                last_config["style_font_keys"]["gothic"],
                config["style_font_keys"]["gothic"],
            )
            self.assertEqual(
                last_config["openai_base_url"],
                config["openai_base_url"],
            )
            self.assertEqual(last_config["openai_model"], config["openai_model"])
            self.assertEqual(
                last_config["advanced_erase_provider"],
                config["advanced_erase_provider"],
            )
            self.assertEqual(
                last_config["advanced_erase_base_url"],
                config["advanced_erase_base_url"],
            )
            self.assertEqual(
                last_config["advanced_erase_model"],
                config["advanced_erase_model"],
            )
            provider_options = last_config["provider_options"]
            self.assertEqual(
                provider_options["base_url"],
                "https://nested-provider.example/v1",
            )
            self.assertEqual(
                provider_options["font_key"],
                "project:NotoSansSC-Bold.otf",
            )
            self.assertEqual(
                provider_options["style_font_keys"]["gothic"],
                "system:SourceHanSansSC-Bold.otf",
            )
            for credential_alias in (
                "accessToken",
                "client secret",
                "APIKey",
                "auth-token",
            ):
                self.assertEqual(provider_options[credential_alias], "")
            self.assertEqual(provider_options["nested"]["access_token"], "")
            self.assertEqual(provider_options["nested"]["auth token"], "")
            self.assertNotIn("fontPath", provider_options["nested"])
            self.assertNotIn("private---dir", provider_options["nested"])
            self.assertNotIn("font_path", last_config)
            self.assertNotIn("style_font_paths", last_config)
            for forbidden_key in (
                "source_dir",
                "translated_dir",
                "rerender_cache_dir",
                "mask_debug_dir",
                "download_path",
            ):
                self.assertNotIn(forbidden_key, last_config)
                self.assertNotIn(f'"{forbidden_key}"'.encode(), raw_manifest)
            for forbidden_literal in (
                b"translator-secret-literal",
                b"cleanup-secret-literal",
                b"erase-secret-literal",
                b"camel-access-token-secret",
                b"spaced-client-secret",
                b"acronym-api-key-secret",
                b"hyphen-auth-token-secret",
                b"exact-access-token-secret",
                b"spaced-auth-token-secret",
                b"private-font-path.otf",
                b".project-working-set-private",
                b".project-working-set-",
                str(root).encode(),
                str(root.resolve()).encode(),
                str(Path(__file__).resolve().parents[2]).encode(),
            ):
                self.assertNotIn(forbidden_literal, raw_manifest)
            self.assertEqual(
                snapshot["project_head_generation"],
                result["project_head_generation"],
            )
            self.assertEqual(
                snapshot["project_head_revision_id"],
                result["project_head_revision_id"],
            )
            self.assertEqual(
                snapshot["project_head_revision_id"],
                engine.project_workspace.read_project_head(project_id)[
                    "revision_id"
                ],
            )

    def test_snapshot_config_sanitizer_canonicalizes_nested_alias_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            sanitized = engine._sanitize_config_for_storage(
                {
                    "provider_options": [
                        {
                            "access__token": "repeated-underscore-access-secret",
                            "AUTH Token": "uppercase-auth-secret",
                            "API---Key": "punctuated-api-secret",
                            "client...secret": "punctuated-client-secret",
                            "refreshToken": "camel-refresh-secret",
                            "font...Path": "/private/runtime-font.otf",
                            "private___dir": "/private/working-set",
                            "base_url": "https://provider.example/v1",
                            "font_key": "project:font.otf",
                            "style_font_keys": {"gothic": "system:font.otf"},
                        }
                    ]
                }
            )

            provider = sanitized["provider_options"][0]
            for credential_alias in (
                "access__token",
                "AUTH Token",
                "API---Key",
                "client...secret",
                "refreshToken",
            ):
                self.assertEqual(provider[credential_alias], "")
            self.assertNotIn("font...Path", provider)
            self.assertNotIn("private___dir", provider)
            self.assertEqual(provider["base_url"], "https://provider.example/v1")
            self.assertEqual(provider["font_key"], "project:font.otf")
            self.assertEqual(
                provider["style_font_keys"],
                {"gothic": "system:font.otf"},
            )
            serialized = json.dumps(sanitized, ensure_ascii=False)
            for forbidden_literal in (
                "repeated-underscore-access-secret",
                "uppercase-auth-secret",
                "punctuated-api-secret",
                "punctuated-client-secret",
                "camel-refresh-secret",
                "/private/runtime-font.otf",
                "/private/working-set",
            ):
                self.assertNotIn(forbidden_literal, serialized)

    def test_project_command_succeeds_with_one_head_commit_when_snapshot_creation_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = "snapshot-create-failure"
            engine, session = self.make_recognized_zero_region_project(
                root,
                project_id=project_id,
                page_ids=["page-1.png"],
            )
            initial_head = engine.project_workspace.read_project_head(project_id)

            with mock.patch.object(
                engine.project_workspace,
                "commit_project_head",
                wraps=engine.project_workspace.commit_project_head,
            ) as commit_head, mock.patch.object(
                engine.project_workspace,
                "create_project_head_snapshot",
                side_effect=OSError("synthetic snapshot create failure"),
            ):
                result = asyncio.run(
                    self.make_workflow_coordinator(engine, session).execute(
                        ProjectCommand(
                            project_id=project_id,
                            action="resume-translate",
                            config={"translator": "none", "target_lang": "CHS"},
                        )
                    )
                )

            committed_head = engine.project_workspace.read_project_head(project_id)
            self.assertEqual(commit_head.call_count, 1)
            self.assertEqual(
                committed_head["generation"],
                initial_head["generation"] + 1,
            )
            self.assertEqual(
                result["project_head_revision_id"],
                committed_head["revision_id"],
            )
            self.assertEqual(result["workflow_stage"], "translated")
            self.assertTrue(
                any(
                    "automatic snapshot/retention failed" in warning
                    and "synthetic snapshot create failure" in warning
                    for warning in result["warnings"]
                )
            )
            self.assertEqual(
                list(
                    engine.project_workspace.project_snapshots_dir(project_id).glob(
                        "*.json"
                    )
                ),
                [],
            )

    def test_project_command_succeeds_with_one_head_commit_when_snapshot_retention_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = "snapshot-retention-failure"
            engine, session = self.make_recognized_zero_region_project(
                root,
                project_id=project_id,
                page_ids=["page-1.png"],
            )
            initial_head = engine.project_workspace.read_project_head(project_id)

            with mock.patch.object(
                engine.project_workspace,
                "commit_project_head",
                wraps=engine.project_workspace.commit_project_head,
            ) as commit_head, mock.patch.object(
                engine.project_workspace,
                "enforce_snapshot_retention",
                side_effect=OSError("synthetic snapshot retention failure"),
            ):
                result = asyncio.run(
                    self.make_workflow_coordinator(engine, session).execute(
                        ProjectCommand(
                            project_id=project_id,
                            action="resume-translate",
                            config={"translator": "none", "target_lang": "CHS"},
                        )
                    )
                )

            committed_head = engine.project_workspace.read_project_head(project_id)
            self.assertEqual(commit_head.call_count, 1)
            self.assertEqual(
                committed_head["generation"],
                initial_head["generation"] + 1,
            )
            self.assertEqual(
                result["project_head_revision_id"],
                committed_head["revision_id"],
            )
            self.assertEqual(result["workflow_stage"], "translated")
            self.assertTrue(
                any(
                    "automatic snapshot/retention failed" in warning
                    and "synthetic snapshot retention failure" in warning
                    for warning in result["warnings"]
                )
            )
            manifest_path = next(
                engine.project_workspace.project_snapshots_dir(project_id).glob(
                    "*.json"
                )
            )
            snapshot = json.loads(manifest_path.read_bytes())
            self.assertEqual(
                snapshot["project_head_generation"],
                committed_head["generation"],
            )
            self.assertEqual(
                snapshot["project_head_revision_id"],
                committed_head["revision_id"],
            )

    def test_zero_text_region_page_translates_and_exports_through_public_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = "zero-region-public-workflow"
            page_id = "page-1.png"
            engine, session = self.make_recognized_zero_region_project(
                root,
                project_id=project_id,
                page_ids=[page_id],
            )
            initial_head = engine.project_workspace.read_project_head(project_id)
            events: list[dict[str, object]] = []

            async def collect_event(event: dict[str, object]) -> None:
                events.append(event)

            result = asyncio.run(
                self.make_workflow_coordinator(engine, session).execute(
                    ProjectCommand(
                        project_id=project_id,
                        action="resume-translate",
                        config={
                        "translator": "none",
                        "target_lang": "CHS",
                        "rerender_output_format": "png",
                        },
                    ),
                    progress=collect_event,
                )
            )

            page_view = engine.build_client_session_payload(
                project_id,
                session,
            )["page_artifacts"][page_id]
            restored_session = engine.restore_project_session(project_id)
            restored_page_view = engine.build_client_session_payload(
                project_id,
                restored_session,
            )["page_artifacts"][page_id]
            final_head = engine.project_workspace.read_project_head(project_id)
            self.assertEqual(
                engine.project_workspace.read_project_page_document(
                    project_id,
                    page_id,
                )["regions"],
                [],
            )
            self.assertTrue(page_view["capabilities"]["recognition_ready"])
            self.assertTrue(page_view["capabilities"]["blank_ready"])
            self.assertTrue(page_view["capabilities"]["translation_ready"])
            self.assertTrue(page_view["capabilities"]["final_ready"])
            self.assertTrue(page_view["capabilities"]["can_export"])
            self.assertTrue(restored_page_view["capabilities"]["can_export"])
            self.assertEqual(result["download_url"], f"/api/download/{project_id}")
            self.assertGreater(final_head["generation"], initial_head["generation"])
            self.assertIn("archive/result.zip", final_head["files"])
            self.assertTrue(Path(session["download_path"]).is_file())
            self.assertNotIn(".project-working-set-", str(session["download_path"]))
            Path(session["download_path"]).unlink()
            Path(session["translated_dir"], page_id).unlink()
            recovered_archive = engine.build_session_archive(
                project_id,
                session,
            )
            self.assertTrue(Path(recovered_archive).is_file())
            self.assertIsNone(
                engine.project_workspace.read_pending_artifact_set(project_id)
            )
            self.assertTrue(
                any(event.get("event") == "progress" for event in events)
            )

    def test_translated_page_retry_repackages_checkpointed_page_from_stable_head_stage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = "translated-page-archive-retry"
            page_id = "page-1.png"
            engine, session = self.make_recognized_zero_region_project(
                root,
                project_id=project_id,
                page_ids=[page_id],
            )
            coordinator = self.make_workflow_coordinator(engine, session)
            config = {
                "translator": "none",
                "target_lang": "CHS",
                "rerender_output_format": "png",
            }
            asyncio.run(
                coordinator.execute(
                    ProjectCommand(
                        project_id=project_id,
                        action="resume-translate",
                        config=config,
                    )
                )
            )
            config = {
                **config,
                "advanced_text_repair": "force",
                "use_gpu": True,
            }
            translated_head = engine.project_workspace.read_project_head(project_id)
            translated_state = engine.project_workspace.read_project_state_from_head(
                project_id,
                translated_head,
            )
            self.assertEqual(translated_state["workflow_stage"], "translated")
            old_archive_path = root / "old-head.zip"
            engine.project_workspace.materialize_project_head_artifact(
                project_id,
                "archive/result.zip",
                old_archive_path,
            )
            old_archive_bytes = old_archive_path.read_bytes()
            old_archive_blob = copy.deepcopy(
                translated_head["files"]["archive/result.zip"]
            )
            render_calls = 0
            repair_calls = 0
            archive_calls = 0
            rendered_color = (17, 34, 51)
            repaired_color = (0, 255, 0)

            async def render_updated_page(*_args, **kwargs) -> None:
                nonlocal render_calls
                render_calls += 1
                output_path = Path(kwargs["output_path"])
                Image.new("RGB", (16, 16), rendered_color).save(output_path)

            async def repair_updated_page(*_args, **kwargs) -> int:
                nonlocal repair_calls
                repair_calls += 1
                self.assertEqual(
                    [image["stored_name"] for image in kwargs["complex_images"]],
                    [page_id],
                )
                Image.new("RGB", (16, 16), repaired_color).save(
                    Path(kwargs["session"]["translated_dir"]) / page_id
                )
                return 1

            def build_updated_archive(*_args, **kwargs) -> str:
                nonlocal archive_calls
                archive_calls += 1
                if archive_calls == 1:
                    raise RuntimeError("synthetic archive build failure")
                current_session = kwargs["session"]
                destination = Path(kwargs["destination"])
                output_name = current_session["translated_output_map"][page_id]
                page_path = Path(current_session["translated_dir"]) / output_name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(destination, "w") as archive:
                    archive.writestr(page_id, page_path.read_bytes())
                return str(destination)

            engine._render_cached_page = render_updated_page  # type: ignore[method-assign]
            engine._enhance_complex_pages = repair_updated_page  # type: ignore[method-assign]
            engine.build_session_archive = build_updated_archive  # type: ignore[method-assign]
            command = ProjectCommand(
                project_id=project_id,
                action="translate-page",
                config=config,
                target_stored_name=page_id,
            )

            with self.assertRaisesRegex(RuntimeError, "archive build failure"):
                asyncio.run(coordinator.execute(command))

            self.assertEqual(
                engine.project_workspace.read_project_head(project_id),
                translated_head,
            )
            pending = engine.project_workspace.read_pending_artifact_set(project_id)
            self.assertIsNotNone(pending)
            self.assertEqual(pending["page_checkpoints"], {page_id: "finalized"})
            pending_output_dir = root / "pending-translated"
            engine.project_workspace.restore_pending_artifact_set(
                project_id,
                pending,
                {"translated": pending_output_dir},
            )
            self.assertEqual(
                Image.open(pending_output_dir / page_id).getpixel((0, 0)),
                repaired_color,
            )

            with mock.patch.object(
                engine.project_workspace,
                "commit_project_head",
                wraps=engine.project_workspace.commit_project_head,
            ) as commit_head:
                result = asyncio.run(coordinator.execute(command))

            self.assertEqual(render_calls, 1)
            self.assertEqual(repair_calls, 1)
            self.assertEqual(archive_calls, 2)
            self.assertEqual(commit_head.call_count, 1)
            self.assertIsNone(
                engine.project_workspace.read_pending_artifact_set(project_id)
            )
            final_head = engine.project_workspace.read_project_head(project_id)
            self.assertEqual(
                final_head["generation"],
                translated_head["generation"] + 1,
            )
            self.assertEqual(result["workflow_stage"], "translated")
            final_state = engine.project_workspace.read_project_state_from_head(
                project_id,
                final_head,
            )
            self.assertEqual(final_state["workflow_stage"], "translated")
            self.assertNotEqual(
                final_head["files"]["archive/result.zip"],
                old_archive_blob,
            )
            final_archive_path = root / "final-head.zip"
            final_page_path = root / "final-page.png"
            engine.project_workspace.materialize_project_head_artifact(
                project_id,
                "archive/result.zip",
                final_archive_path,
            )
            engine.project_workspace.materialize_project_head_artifact(
                project_id,
                f"translated/{page_id}",
                final_page_path,
            )
            self.assertEqual(
                Image.open(final_page_path).getpixel((0, 0)),
                repaired_color,
            )
            self.assertNotEqual(final_archive_path.read_bytes(), old_archive_bytes)
            with zipfile.ZipFile(final_archive_path) as archive:
                self.assertEqual(archive.read(page_id), final_page_path.read_bytes())

    def test_detected_page_translation_stays_detected_without_building_archive(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = "detected-page-no-archive"
            page_id = "page-1.png"
            engine, session = self.make_recognized_zero_region_project(
                root,
                project_id=project_id,
                page_ids=[page_id],
            )
            initial_head = engine.project_workspace.read_project_head(project_id)
            archive_calls = 0

            async def render_page(*_args, **kwargs) -> None:
                Image.new("RGB", (16, 16), (68, 85, 102)).save(
                    kwargs["output_path"]
                )

            def reject_archive(*_args, **_kwargs) -> str:
                nonlocal archive_calls
                archive_calls += 1
                raise AssertionError(
                    "a detected Head page translation must not build an archive"
                )

            engine._render_cached_page = render_page  # type: ignore[method-assign]
            engine.build_session_archive = reject_archive  # type: ignore[method-assign]
            result = asyncio.run(
                self.make_workflow_coordinator(engine, session).execute(
                    ProjectCommand(
                        project_id=project_id,
                        action="translate-page",
                        config={
                            "translator": "none",
                            "target_lang": "CHS",
                            "rerender_output_format": "png",
                        },
                        target_stored_name=page_id,
                    )
                )
            )

            self.assertEqual(archive_calls, 0)
            self.assertEqual(result["workflow_stage"], "detected")
            final_head = engine.project_workspace.read_project_head(project_id)
            self.assertEqual(
                final_head["generation"],
                initial_head["generation"] + 1,
            )
            final_state = engine.project_workspace.read_project_state_from_head(
                project_id,
                final_head,
            )
            self.assertEqual(final_state["workflow_stage"], "detected")
            self.assertNotIn("archive/result.zip", final_head["files"])
            self.assertIsNone(
                engine.project_workspace.read_pending_artifact_set(project_id)
            )

    def test_project_retry_only_skips_pages_verified_by_matching_pending_checkpoint(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = "translated-project-explicit-checkpoint-retry"
            page_ids = ["page-1.png", "page-2.png"]
            engine, session = self.make_recognized_zero_region_project(
                root,
                project_id=project_id,
                page_ids=page_ids,
            )
            coordinator = self.make_workflow_coordinator(engine, session)
            config = {
                "translator": "none",
                "target_lang": "CHS",
                "rerender_output_format": "png",
                "advanced_text_repair": "off",
            }
            asyncio.run(
                coordinator.execute(
                    ProjectCommand(
                        project_id=project_id,
                        action="resume-translate",
                        config=config,
                    )
                )
            )
            command_base = engine.project_workspace.read_project_command_base(
                project_id
            )
            translated_page_documents = copy.deepcopy(command_base.page_documents)
            for page_id in page_ids:
                translated_page_documents[page_id]["regions"] = [
                    {
                        "region_id": f"translated::{page_id}",
                        "source_text": "原文",
                        "translation": {
                            "machine": "Translation",
                            "resolved": "Translation",
                        },
                        "flags": {},
                    }
                ]
            engine.project_workspace.commit_project_head(
                project_id,
                state_document=command_base.state_document,
                project_manifest=command_base.project_manifest,
                page_documents=translated_page_documents,
                expected_generation=command_base.head_generation,
                expected_revision_id=command_base.head_revision_id,
            )
            for page_id, document in translated_page_documents.items():
                engine.project_workspace.write_json_file(
                    engine.project_workspace.project_page_document_path(
                        project_id,
                        page_id,
                    ),
                    document,
                )
            translated_head = engine.project_workspace.read_project_head(project_id)
            translated_state = engine.project_workspace.read_project_state_from_head(
                project_id,
                translated_head,
            )
            self.assertEqual(translated_state["workflow_stage"], "translated")
            config = {
                **config,
                "advanced_text_repair": "force",
                "use_gpu": True,
            }
            rendered_by_attempt: dict[int, list[str]] = {1: [], 2: []}
            attempt = 1
            archive_calls = 0
            repaired_scopes: list[list[str]] = []

            async def render_page(*_args, **kwargs) -> None:
                page_id = Path(kwargs["output_path"]).name
                rendered_by_attempt[attempt].append(page_id)
                if attempt == 1 and page_id == "page-2.png":
                    raise RuntimeError("synthetic second-page render failure")
                color = (17, 34, 51) if page_id == "page-1.png" else (68, 85, 102)
                Image.new("RGB", (16, 16), color).save(kwargs["output_path"])

            def build_archive(*_args, **kwargs) -> str:
                nonlocal archive_calls
                archive_calls += 1
                current_session = kwargs["session"]
                destination = Path(kwargs["destination"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(destination, "w") as archive:
                    for page_id in page_ids:
                        output_name = current_session["translated_output_map"][page_id]
                        output_path = (
                            Path(current_session["translated_dir"]) / output_name
                        )
                        archive.writestr(page_id, output_path.read_bytes())
                return str(destination)

            async def repair_pages(*_args, **kwargs) -> int:
                repaired_scopes.append(
                    [image["stored_name"] for image in kwargs["complex_images"]]
                )
                return len(kwargs["complex_images"])

            engine._render_cached_page = render_page  # type: ignore[method-assign]
            engine._enhance_complex_pages = repair_pages  # type: ignore[method-assign]
            engine.build_session_archive = build_archive  # type: ignore[method-assign]
            command = ProjectCommand(
                project_id=project_id,
                action="resume-translate",
                config=config,
            )

            with self.assertRaisesRegex(RuntimeError, "second-page render failure"):
                asyncio.run(coordinator.execute(command))

            self.assertEqual(
                rendered_by_attempt[1],
                ["page-1.png", "page-2.png"],
            )
            self.assertEqual(
                engine.project_workspace.read_project_head(project_id),
                translated_head,
            )
            pending = engine.project_workspace.read_pending_artifact_set(project_id)
            self.assertIsNotNone(pending)
            self.assertEqual(
                pending["page_checkpoints"],
                {"page-1.png": "rendered"},
            )

            attempt = 2
            with mock.patch.object(
                engine.project_workspace,
                "commit_project_head",
                wraps=engine.project_workspace.commit_project_head,
            ) as commit_head:
                result = asyncio.run(coordinator.execute(command))

            self.assertEqual(rendered_by_attempt[2], ["page-2.png"])
            self.assertEqual(repaired_scopes, [page_ids])
            self.assertEqual(archive_calls, 1)
            self.assertEqual(commit_head.call_count, 1)
            self.assertIsNone(
                engine.project_workspace.read_pending_artifact_set(project_id)
            )
            final_head = engine.project_workspace.read_project_head(project_id)
            self.assertEqual(
                final_head["generation"],
                translated_head["generation"] + 1,
            )
            self.assertEqual(result["workflow_stage"], "translated")
            archive_path = root / "retried-project.zip"
            engine.project_workspace.materialize_project_head_artifact(
                project_id,
                "archive/result.zip",
                archive_path,
            )
            with zipfile.ZipFile(archive_path) as archive:
                for page_id in page_ids:
                    final_page_path = root / f"final-{page_id}"
                    engine.project_workspace.materialize_project_head_artifact(
                        project_id,
                        f"translated/{page_id}",
                        final_page_path,
                    )
                    self.assertEqual(
                        archive.read(page_id),
                        final_page_path.read_bytes(),
                    )

    def test_interrupted_project_translation_keeps_head_and_only_verified_pending_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = "partial-public-workflow"
            page_ids = ["page-1.png", "page-2.png"]
            engine, session = self.make_recognized_zero_region_project(
                root,
                project_id=project_id,
                page_ids=page_ids,
            )
            initial_head = engine.project_workspace.read_project_head(project_id)
            events = self.interrupt_translation_after_first_verified_page(
                engine,
                project_id=project_id,
                session=session,
                config={
                    "translator": "none",
                    "target_lang": "CHS",
                    "rerender_output_format": "png",
                },
            )

            head_after_failure = engine.project_workspace.read_project_head(project_id)
            pending = engine.project_workspace.read_pending_artifact_set(project_id)
            visible_state_document = (
                engine.project_workspace.read_project_session_document(project_id)
            )
            visible_artifacts = ProjectArtifactState.model_validate(
                visible_state_document["artifact_state"]
            )
            self.assertEqual(
                head_after_failure,
                initial_head,
            )
            self.assertEqual(
                pending["page_checkpoints"],
                {"page-1.png": "rendered"},
            )
            self.assertEqual(
                pending["state_document"]["translated_output_map"],
                {"page-1.png": "page-1.png"},
            )
            self.assertFalse(
                visible_artifacts.page_view("page-1.png").capabilities.translation_ready
            )
            self.assertFalse(
                visible_artifacts.page_view("page-2.png").capabilities.translation_ready
            )
            self.assertFalse(
                (
                    engine.project_workspace.project_translated_dir(project_id)
                    / "page-1.png"
                ).exists()
            )
            progress_events = [
                event for event in events if event.get("event") == "progress"
            ]
            self.assertEqual(
                [event.get("stored_name") for event in progress_events],
                ["page-1.png"],
            )

    def test_repair_cancellation_retries_only_the_unfinalized_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = "repair-cancellation-page-checkpoints"
            page_ids = ["page-1.png", "page-2.png"]
            engine, session = self.make_recognized_zero_region_project(
                root,
                project_id=project_id,
                page_ids=page_ids,
            )
            coordinator = self.make_workflow_coordinator(engine, session)
            render_calls: list[str] = []
            repair_scopes: list[list[str]] = []
            repair_attempt = 1

            async def render_page(*_args, **kwargs) -> None:
                page_id = Path(kwargs["output_path"]).name
                render_calls.append(page_id)
                Image.new("RGB", (16, 16), (20, 40, 60)).save(
                    kwargs["output_path"]
                )

            async def repair_pages(*_args, **kwargs) -> int:
                nonlocal repair_attempt
                scope = [
                    image["stored_name"] for image in kwargs["complex_images"]
                ]
                repair_scopes.append(scope)
                finalize = kwargs["page_finalized_callback"]
                if repair_attempt == 1:
                    finalize(page_ids[0])
                    raise asyncio.CancelledError()
                finalize(page_ids[1])
                return 1

            def build_archive(*_args, **kwargs) -> str:
                destination = Path(kwargs["destination"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(destination, "w") as archive:
                    for page_id in page_ids:
                        archive.write(
                            Path(kwargs["session"]["translated_dir"]) / page_id,
                            page_id,
                        )
                return str(destination)

            engine._render_cached_page = render_page  # type: ignore[method-assign]
            engine._enhance_complex_pages = repair_pages  # type: ignore[method-assign]
            engine.build_session_archive = build_archive  # type: ignore[method-assign]
            command = ProjectCommand(
                project_id=project_id,
                action="resume-translate",
                config={
                    "translator": "none",
                    "target_lang": "CHS",
                    "rerender_output_format": "png",
                    "advanced_text_repair": "force",
                    "use_gpu": True,
                },
            )

            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(coordinator.execute(command))

            pending = engine.project_workspace.read_pending_artifact_set(project_id)
            self.assertEqual(
                pending["page_checkpoints"],
                {
                    "page-1.png": "finalized",
                    "page-2.png": "rendered",
                },
            )
            self.assertEqual(render_calls, page_ids)

            repair_attempt = 2
            result = asyncio.run(coordinator.execute(command))

            self.assertEqual(render_calls, page_ids)
            self.assertEqual(repair_scopes, [page_ids, ["page-2.png"]])
            self.assertEqual(result["workflow_stage"], "translated")
            self.assertIsNone(
                engine.project_workspace.read_pending_artifact_set(project_id)
            )

    def test_pending_checkpoint_claims_must_match_state_and_artifacts(self) -> None:
        config = {
            "translator": "none",
            "target_lang": "CHS",
            "rerender_output_format": "png",
        }
        for corruption in ("unverified-page", "missing-state", "missing-artifact"):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as tmp:
                project_id = f"corrupt-checkpoint-{corruption}"
                engine, session = self.make_recognized_zero_region_project(
                    Path(tmp),
                    project_id=project_id,
                    page_ids=["page-1.png", "page-2.png"],
                )
                initial_head = engine.project_workspace.read_project_head(project_id)
                self.interrupt_translation_after_first_verified_page(
                    engine,
                    project_id=project_id,
                    session=session,
                    config=config,
                )
                pending_path = engine.project_workspace.project_pending_artifact_path(
                    project_id
                )
                pending = engine.project_workspace.read_json_file(pending_path, {})
                if corruption == "unverified-page":
                    pending["page_checkpoints"]["page-2.png"] = "rendered"
                elif corruption == "missing-state":
                    pending["state_document"]["translated_output_map"].pop(
                        "page-1.png"
                    )
                else:
                    pending["artifact_bundle"]["files"].pop(
                        "translated/page-1.png"
                    )
                engine.project_workspace.write_json_file(pending_path, pending)
                diagnostic_evidence = pending_path.read_bytes()

                with self.assertRaises(CorruptProjectArtifactError):
                    asyncio.run(
                        self.make_workflow_coordinator(engine, session).execute(
                            ProjectCommand(
                                project_id=project_id,
                                action="resume-translate",
                                config=config,
                            )
                        )
                    )

                self.assertEqual(
                    engine.project_workspace.read_project_head(project_id),
                    initial_head,
                )
                self.assertEqual(pending_path.read_bytes(), diagnostic_evidence)

    def test_legacy_translate_detection_checkpoint_renders_and_rewrites_v2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = "translate-detection-checkpoint"
            engine, session = self.make_recognized_zero_region_project(
                root,
                project_id=project_id,
                page_ids=["page-1.png"],
            )
            config = {
                "translator": "none",
                "target_lang": "CHS",
                "rerender_output_format": "png",
                "advanced_text_repair": "off",
            }
            head = engine.project_workspace.read_project_head(project_id)
            state_document = engine.project_workspace.read_project_state_from_head(
                project_id,
                head,
            )
            page_cache_dir = Path(
                state_document["rerender_cache_dir"]
            ) / "page-1.png"

            engine.project_workspace.write_pending_artifact_set(
                project_id,
                action="translate",
                resume_fingerprint=engine.project_command_fingerprint(
                    action="translate",
                    raw_config=config,
                    target_stored_name=None,
                ),
                base_head=head,
                state_document=state_document,
                files={
                    "cache/page-1.png/regions.json": page_cache_dir / "regions.json",
                    "cache/page-1.png/meta.json": page_cache_dir / "meta.json",
                    "cache/page-1.png/inpainted.png": page_cache_dir / "inpainted.png",
                },
                metadata={
                    "page_checkpoints": {"page-1.png": "detected"},
                    "state_validated": True,
                },
            )
            pending_path = engine.project_workspace.project_pending_artifact_path(
                project_id
            )
            legacy_pending = engine.project_workspace.read_json_file(
                pending_path,
                {},
            )
            legacy_pending["schema_version"] = 1
            legacy_pending["completed_page_ids"] = ["page-1.png"]
            legacy_pending.pop("page_checkpoints", None)
            legacy_pending.pop("state_validated", None)
            engine.project_workspace.write_json_file(pending_path, legacy_pending)

            pending = engine.project_workspace.read_pending_artifact_set(project_id)

            self.assertEqual(pending["schema_version"], 1)
            self.assertEqual(
                pending["page_checkpoints"],
                {"page-1.png": "detected"},
            )
            render_calls = 0

            async def render_page(*_args, **kwargs) -> None:
                nonlocal render_calls
                render_calls += 1
                Image.new("RGB", (16, 16), (30, 60, 90)).save(
                    kwargs["output_path"]
                )

            def fail_archive(*_args, **_kwargs) -> str:
                raise RuntimeError("synthetic legacy detection archive failure")

            engine._render_cached_page = render_page  # type: ignore[method-assign]
            engine.build_session_archive = fail_archive  # type: ignore[method-assign]

            with self.assertRaisesRegex(RuntimeError, "legacy detection archive"):
                asyncio.run(
                    self.make_workflow_coordinator(engine, session).execute(
                        ProjectCommand(
                            project_id=project_id,
                            action="translate",
                            config=config,
                        )
                    )
                )

            rewritten = engine.project_workspace.read_pending_artifact_set(project_id)
            self.assertEqual(render_calls, 1)
            self.assertEqual(rewritten["schema_version"], 2)
            self.assertEqual(
                rewritten["page_checkpoints"],
                {"page-1.png": "finalized"},
            )

    def test_legacy_translating_checkpoint_reuses_render_but_retries_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = "legacy-rendered-repair-retry"
            page_id = "page-1.png"
            engine, session = self.make_recognized_zero_region_project(
                root,
                project_id=project_id,
                page_ids=[page_id],
            )
            config = {
                "translator": "none",
                "target_lang": "CHS",
                "rerender_output_format": "png",
                "advanced_text_repair": "force",
                "use_gpu": True,
            }
            command = ProjectCommand(
                project_id=project_id,
                action="resume-translate",
                config=config,
            )
            coordinator = self.make_workflow_coordinator(engine, session)
            render_calls = 0
            repair_calls = 0

            async def render_page(*_args, **kwargs) -> None:
                nonlocal render_calls
                render_calls += 1
                Image.new("RGB", (16, 16), (25, 50, 75)).save(
                    kwargs["output_path"]
                )

            async def repair_page(*_args, **_kwargs) -> int:
                nonlocal repair_calls
                repair_calls += 1
                if repair_calls == 1:
                    raise RuntimeError("synthetic repair interruption")
                return 1

            def fail_archive(*_args, **_kwargs) -> str:
                raise RuntimeError("synthetic legacy rendered archive failure")

            engine._render_cached_page = render_page  # type: ignore[method-assign]
            engine._enhance_complex_pages = repair_page  # type: ignore[method-assign]
            engine.build_session_archive = fail_archive  # type: ignore[method-assign]

            with self.assertRaisesRegex(RuntimeError, "repair interruption"):
                asyncio.run(coordinator.execute(command))

            pending_path = engine.project_workspace.project_pending_artifact_path(
                project_id
            )
            legacy_pending = engine.project_workspace.read_json_file(
                pending_path,
                {},
            )
            self.assertEqual(
                legacy_pending["page_checkpoints"],
                {page_id: "rendered"},
            )
            legacy_pending["schema_version"] = 1
            legacy_pending["completed_page_ids"] = [page_id]
            legacy_pending.pop("page_checkpoints", None)
            legacy_pending.pop("state_validated", None)
            engine.project_workspace.write_json_file(pending_path, legacy_pending)

            with self.assertRaisesRegex(RuntimeError, "legacy rendered archive"):
                asyncio.run(coordinator.execute(command))

            rewritten = engine.project_workspace.read_pending_artifact_set(project_id)
            self.assertEqual(render_calls, 1)
            self.assertEqual(repair_calls, 2)
            self.assertEqual(rewritten["schema_version"], 2)
            self.assertEqual(
                rewritten["page_checkpoints"],
                {page_id: "finalized"},
            )

    def test_detected_checkpoint_map_remains_monotonic_during_partial_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = "detected-to-rendered-monotonic"
            page_ids = ["page-1.png", "page-2.png"]
            engine, session = self.make_recognized_zero_region_project(
                root,
                project_id=project_id,
                page_ids=page_ids,
            )
            config = {
                "translator": "none",
                "target_lang": "CHS",
                "rerender_output_format": "png",
                "advanced_text_repair": "off",
            }
            head = engine.project_workspace.read_project_head(project_id)
            state_document = engine.project_workspace.read_project_state_from_head(
                project_id,
                head,
            )
            cache_root = Path(state_document["rerender_cache_dir"])
            checkpoint_files = {
                f"cache/{page_id}/{file_name}": cache_root / page_id / file_name
                for page_id in page_ids
                for file_name in ("regions.json", "meta.json", "inpainted.png")
            }
            engine.project_workspace.write_pending_artifact_set(
                project_id,
                action="translate",
                resume_fingerprint=engine.project_command_fingerprint(
                    action="translate",
                    raw_config=config,
                    target_stored_name=None,
                ),
                base_head=head,
                state_document=state_document,
                files=checkpoint_files,
                metadata={
                    "page_checkpoints": {
                        page_id: "detected" for page_id in page_ids
                    }
                },
            )

            async def render_page(*_args, **kwargs) -> None:
                page_id = Path(kwargs["output_path"]).name
                if page_id == "page-2.png":
                    raise RuntimeError("synthetic partial render failure")
                Image.new("RGB", (16, 16), (40, 80, 120)).save(
                    kwargs["output_path"]
                )

            engine._render_cached_page = render_page  # type: ignore[method-assign]
            with self.assertRaisesRegex(RuntimeError, "partial render failure"):
                asyncio.run(
                    self.make_workflow_coordinator(engine, session).execute(
                        ProjectCommand(
                            project_id=project_id,
                            action="translate",
                            config=config,
                        )
                    )
                )

            pending = engine.project_workspace.read_pending_artifact_set(project_id)
            self.assertEqual(
                pending["page_checkpoints"],
                {
                    "page-1.png": "rendered",
                    "page-2.png": "detected",
                },
            )

    def test_detection_pending_requires_canonical_recoverable_page_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = "detection-checkpoint-cache-contract"
            engine, _session = self.make_recognized_zero_region_project(
                root,
                project_id=project_id,
                page_ids=["page-1.png"],
            )
            head = engine.project_workspace.read_project_head(project_id)
            state_document = engine.project_workspace.read_project_state_from_head(
                project_id,
                head,
            )
            irrelevant_cache = root / "debug.txt"
            irrelevant_cache.write_text("not a page cache", encoding="utf-8")
            with self.assertRaises(CorruptProjectArtifactError):
                engine.project_workspace.write_pending_artifact_set(
                    project_id,
                    action="detect",
                    resume_fingerprint="detect-command",
                    base_head=head,
                    state_document=state_document,
                    files={"cache/page-1.png/debug.txt": irrelevant_cache},
                    metadata={
                        "page_checkpoints": {"page-1.png": "detected"},
                        "state_validated": True,
                    },
                )

    def test_pending_action_and_workflow_stage_matrix_is_strict(self) -> None:
        allowed_stages = {
            "detect": {"detecting", "detected"},
            "translate": {"detecting", "detected", "translating"},
            "resume-translate": {"translating"},
            "translate-page": {"translating"},
            "rerender": {"translated"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            project_id = "pending-action-stage-matrix"
            config = {
                "translator": "none",
                "target_lang": "CHS",
                "rerender_output_format": "png",
            }
            engine, session = self.make_recognized_zero_region_project(
                Path(tmp),
                project_id=project_id,
                page_ids=["page-1.png", "page-2.png"],
            )
            self.interrupt_translation_after_first_verified_page(
                engine,
                project_id=project_id,
                session=session,
                config=config,
            )
            pending_path = engine.project_workspace.project_pending_artifact_path(
                project_id
            )
            original_pending = engine.project_workspace.read_json_file(
                pending_path,
                {},
            )

            for action, allowed in allowed_stages.items():
                for workflow_stage in (
                    "idle",
                    "detecting",
                    "detected",
                    "translating",
                    "translated",
                ):
                    with self.subTest(action=action, workflow_stage=workflow_stage):
                        pending = copy.deepcopy(original_pending)
                        pending["schema_version"] = 1
                        pending["completed_page_ids"] = ["page-1.png"]
                        pending.pop("page_checkpoints", None)
                        pending.pop("state_validated", None)
                        pending["action"] = action
                        pending["state_document"]["workflow_stage"] = workflow_stage
                        engine.project_workspace.write_json_file(pending_path, pending)
                        if workflow_stage in allowed:
                            restored = (
                                engine.project_workspace.read_pending_artifact_set(
                                    project_id
                                )
                            )
                            self.assertEqual(restored["action"], action)
                            expected_page_stage = (
                                "detected"
                                if workflow_stage in {"detecting", "detected"}
                                else (
                                    "finalized"
                                    if action == "rerender"
                                    else "rendered"
                                )
                            )
                            self.assertEqual(
                                restored["page_checkpoints"],
                                {"page-1.png": expected_page_stage},
                            )
                        else:
                            with self.assertRaises(CorruptProjectArtifactError):
                                engine.project_workspace.read_pending_artifact_set(
                                    project_id
                                )

    def test_translated_pending_requires_complete_decodable_page_artifacts(self) -> None:
        corruptions = (
            "missing-regions",
            "corrupt-regions",
            "unsafe-region-element",
            "missing-meta",
            "corrupt-meta",
            "unknown-base-kind",
            "missing-inpainted",
            "corrupt-inpainted",
            "bool-region-count",
            "mismatched-region-count",
            "missing-translated",
            "corrupt-translated",
            "truncated-translated-jpeg",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = "pending-page-artifact-contract"
            config = {
                "translator": "none",
                "target_lang": "CHS",
                "rerender_output_format": "png",
            }
            engine, session = self.make_recognized_zero_region_project(
                root,
                project_id=project_id,
                page_ids=["page-1.png", "page-2.png"],
            )
            self.interrupt_translation_after_first_verified_page(
                engine,
                project_id=project_id,
                session=session,
                config=config,
            )
            workspace = engine.project_workspace
            pending_path = workspace.project_pending_artifact_path(project_id)
            self.assertIsNotNone(workspace.read_pending_artifact_set(project_id))
            original_pending = workspace.read_json_file(pending_path, {})
            jpeg_buffer = BytesIO()
            Image.new("RGB", (64, 64), (123, 45, 67)).save(
                jpeg_buffer,
                format="JPEG",
                quality=95,
            )
            truncated_jpeg = jpeg_buffer.getvalue()[:-2]
            with Image.open(BytesIO(truncated_jpeg)) as image:
                image.verify()
            with self.assertRaises(OSError):
                with Image.open(BytesIO(truncated_jpeg)) as image:
                    image.load()

            def replace_artifact(
                pending: dict[str, object],
                logical_path: str,
                payload: bytes,
            ) -> None:
                replacement = root / f"replacement-{uuid.uuid4().hex}"
                replacement.write_bytes(payload)
                artifact_bundle = pending["artifact_bundle"]
                original_paths = set(artifact_bundle["files"])
                replacement_bundle = workspace.capture_snapshot_artifacts(
                    project_id,
                    {logical_path: replacement},
                    previous_bundle=artifact_bundle,
                )
                artifact_bundle["files"][logical_path] = replacement_bundle["files"][
                    logical_path
                ]
                self.assertEqual(set(artifact_bundle["files"]), original_paths)

            for corruption in corruptions:
                with self.subTest(corruption=corruption):
                    pending = copy.deepcopy(original_pending)
                    files = pending["artifact_bundle"]["files"]
                    if corruption == "missing-regions":
                        files.pop("cache/page-1.png/regions.json")
                    elif corruption == "corrupt-regions":
                        replace_artifact(
                            pending,
                            "cache/page-1.png/regions.json",
                            b"{not-json",
                        )
                    elif corruption == "unsafe-region-element":
                        replace_artifact(
                            pending,
                            "cache/page-1.png/regions.json",
                            b"[42]",
                        )
                    elif corruption == "missing-meta":
                        files.pop("cache/page-1.png/meta.json")
                    elif corruption == "corrupt-meta":
                        replace_artifact(
                            pending,
                            "cache/page-1.png/meta.json",
                            b"[]",
                        )
                    elif corruption == "unknown-base-kind":
                        replace_artifact(
                            pending,
                            "cache/page-1.png/meta.json",
                            json.dumps({"base_kind": "mystery"}).encode(),
                        )
                    elif corruption == "missing-inpainted":
                        files.pop("cache/page-1.png/inpainted.png")
                    elif corruption == "corrupt-inpainted":
                        replace_artifact(
                            pending,
                            "cache/page-1.png/inpainted.png",
                            b"not-an-image",
                        )
                    elif corruption == "bool-region-count":
                        replace_artifact(
                            pending,
                            "cache/page-1.png/meta.json",
                            json.dumps(
                                {
                                    "base_kind": "inpainted",
                                    "inpainting_region_count": True,
                                }
                            ).encode(),
                        )
                    elif corruption == "mismatched-region-count":
                        replace_artifact(
                            pending,
                            "cache/page-1.png/meta.json",
                            json.dumps(
                                {
                                    "base_kind": "inpainted",
                                    "inpainting_region_count": 1,
                                }
                            ).encode(),
                        )
                    elif corruption == "missing-translated":
                        files.pop("translated/page-1.png")
                    elif corruption == "truncated-translated-jpeg":
                        replace_artifact(
                            pending,
                            "translated/page-1.png",
                            truncated_jpeg,
                        )
                    else:
                        replace_artifact(
                            pending,
                            "translated/page-1.png",
                            b"not-an-image",
                        )
                    workspace.write_json_file(pending_path, pending)

                    with self.assertRaises(CorruptProjectArtifactError):
                        workspace.read_pending_artifact_set(project_id)

    def test_source_no_text_pending_cache_is_valid_without_inpainted_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = "source-no-text-pending"
            engine, _session = self.make_recognized_zero_region_project(
                root,
                project_id=project_id,
                page_ids=["page-1.png"],
            )
            workspace = engine.project_workspace
            head = workspace.read_project_head(project_id)
            state_document = workspace.read_project_state_from_head(project_id, head)
            regions_path = root / "source-no-text-regions.json"
            meta_path = root / "source-no-text-meta.json"
            regions_path.write_text("[]", encoding="utf-8")
            meta_path.write_text(
                json.dumps(
                    {
                        "base_kind": "source_no_text",
                        "inpainting_region_count": 0,
                    }
                ),
                encoding="utf-8",
            )
            workspace.write_pending_artifact_set(
                project_id,
                action="detect",
                resume_fingerprint="detect-source-no-text",
                base_head=head,
                state_document=state_document,
                files={
                    "cache/page-1.png/regions.json": regions_path,
                    "cache/page-1.png/meta.json": meta_path,
                },
                metadata={
                    "page_checkpoints": {"page-1.png": "detected"},
                    "state_validated": True,
                },
            )

            pending = workspace.read_pending_artifact_set(project_id)

            self.assertEqual(
                pending["page_checkpoints"],
                {"page-1.png": "detected"},
            )

    def test_project_preparation_never_persists_through_glossary_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_id = "glossary-private-preparation"
            engine, session = self.make_recognized_zero_region_project(
                Path(tmp),
                project_id=project_id,
                page_ids=["page-1.png"],
            )
            session["project_glossary"] = {
                "entries": [],
                "auto_extract_completed": False,
            }
            engine.persist_project_state(
                project_id,
                session,
                persist_page_documents=True,
            )
            observed_persist_values: list[bool] = []

            async def fake_extract(
                _project_id,
                working_session,
                _config,
                progress_callback=None,
                force=False,
                persist=True,
            ):
                observed_persist_values.append(persist)
                working_session["project_glossary"] = {
                    "entries": [],
                    "auto_extract_completed": True,
                }
                return {"entries": []}

            with mock.patch.object(
                engine,
                "extract_project_glossary",
                side_effect=fake_extract,
            ), mock.patch.object(
                engine,
                "persist_project_state",
                side_effect=AssertionError("Working Set attempted an early persist"),
            ):
                result = asyncio.run(
                    self.make_workflow_coordinator(engine, session).execute(
                        ProjectCommand(
                            project_id=project_id,
                            action="resume-translate",
                            config={
                                "translator": "none",
                                "target_lang": "CHS",
                                "rerender_output_format": "png",
                            },
                        )
                    )
                )

            self.assertEqual(observed_persist_values, [False])
            self.assertEqual(result["workflow_stage"], "translated")

    def test_compatible_retry_resumes_after_verified_pending_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_id = "compatible-public-retry"
            config = {
                "translator": "none",
                "target_lang": "CHS",
                "rerender_output_format": "png",
            }
            engine, session = self.make_recognized_zero_region_project(
                Path(tmp),
                project_id=project_id,
                page_ids=["page-1.png", "page-2.png"],
            )
            self.interrupt_translation_after_first_verified_page(
                engine,
                project_id=project_id,
                session=session,
                config=config,
            )
            retry_events: list[dict[str, object]] = []

            async def collect_retry_event(event: dict[str, object]) -> None:
                retry_events.append(event)

            asyncio.run(
                self.make_workflow_coordinator(engine, session).execute(
                    ProjectCommand(
                        project_id=project_id,
                        action="resume-translate",
                        config=config,
                    ),
                    progress=collect_retry_event,
                )
            )

            progress_page_ids = [
                event.get("stored_name")
                for event in retry_events
                if event.get("event") == "progress"
            ]
            self.assertEqual(progress_page_ids, ["page-2.png"])
            self.assertEqual(
                next(
                    event["total_pages"]
                    for event in retry_events
                    if event.get("event") == "start"
                ),
                1,
            )
            self.assertIsNone(
                engine.project_workspace.read_pending_artifact_set(project_id)
            )

    def test_retry_with_changed_config_starts_from_project_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_id = "changed-config-public-retry"
            initial_config = {
                "translator": "none",
                "target_lang": "CHS",
                "rerender_output_format": "png",
            }
            engine, session = self.make_recognized_zero_region_project(
                Path(tmp),
                project_id=project_id,
                page_ids=["page-1.png", "page-2.png"],
            )
            self.interrupt_translation_after_first_verified_page(
                engine,
                project_id=project_id,
                session=session,
                config=initial_config,
            )
            retry_events: list[dict[str, object]] = []

            async def collect_retry_event(event: dict[str, object]) -> None:
                retry_events.append(event)

            asyncio.run(
                self.make_workflow_coordinator(engine, session).execute(
                    ProjectCommand(
                        project_id=project_id,
                        action="resume-translate",
                        config={**initial_config, "target_lang": "CHT"},
                    ),
                    progress=collect_retry_event,
                )
            )

            progress_page_ids = [
                event.get("stored_name")
                for event in retry_events
                if event.get("event") == "progress"
            ]
            self.assertEqual(progress_page_ids, ["page-1.png", "page-2.png"])
            self.assertEqual(
                next(
                    event["total_pages"]
                    for event in retry_events
                    if event.get("event") == "start"
                ),
                2,
            )

    def test_retry_with_changed_scope_or_base_head_starts_from_page_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {
                "translator": "none",
                "target_lang": "CHS",
                "rerender_output_format": "png",
            }
            for changed_dimension in ("scope", "base-head"):
                with self.subTest(changed_dimension=changed_dimension):
                    project_id = f"changed-{changed_dimension}-public-retry"
                    engine, session = self.make_recognized_zero_region_project(
                        root / changed_dimension,
                        project_id=project_id,
                        page_ids=["page-1.png", "page-2.png"],
                    )
                    self.interrupt_translation_after_first_verified_page(
                        engine,
                        project_id=project_id,
                        session=session,
                        config=config,
                    )
                    pending_before_retry = (
                        engine.project_workspace.read_pending_artifact_set(project_id)
                    )
                    target_stored_name = None
                    expected_total = 2
                    if changed_dimension == "scope":
                        target_stored_name = "page-1.png"
                        expected_total = 1
                    else:
                        engine.update_project_metadata(
                            project_id,
                            session,
                            title="Advanced Project Head",
                        )

                    retry_events: list[dict[str, object]] = []

                    async def collect_retry_event(event: dict[str, object]) -> None:
                        retry_events.append(event)

                    asyncio.run(
                        self.make_workflow_coordinator(engine, session).execute(
                            ProjectCommand(
                                project_id=project_id,
                                action=(
                                    "translate-page"
                                    if target_stored_name
                                    else "resume-translate"
                                ),
                                config=config,
                                target_stored_name=target_stored_name,
                            ),
                            progress=collect_retry_event,
                        )
                    )

                    progress_page_ids = [
                        event.get("stored_name")
                        for event in retry_events
                        if event.get("event") == "progress"
                    ]
                    self.assertEqual(
                        pending_before_retry["page_checkpoints"],
                        {"page-1.png": "rendered"},
                    )
                    self.assertEqual(progress_page_ids[0], "page-1.png")
                    self.assertEqual(
                        next(
                            event["total_pages"]
                            for event in retry_events
                            if event.get("event") == "start"
                        ),
                        expected_total,
                    )
                    if changed_dimension == "scope":
                        committed_state = ProjectArtifactState.model_validate(
                            engine.project_workspace.read_project_session_document(
                                project_id
                            )["artifact_state"]
                        )
                        self.assertEqual(
                            committed_state.pages["page-1.png"].translation.revision,
                            1,
                        )

    def test_retry_with_changed_action_does_not_reuse_verified_pending_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_id = "changed-action-public-retry"
            config = {
                "translator": "none",
                "target_lang": "CHS",
                "rerender_output_format": "png",
            }
            engine, session = self.make_recognized_zero_region_project(
                Path(tmp),
                project_id=project_id,
                page_ids=["page-1.png", "page-2.png"],
            )
            self.interrupt_translation_after_first_verified_page(
                engine,
                project_id=project_id,
                session=session,
                config=config,
            )
            rerender_events: list[dict[str, object]] = []

            async def observe_changed_action(event: dict[str, object]) -> None:
                rerender_events.append(event)

            async def fail_render(*_args, **_kwargs):
                raise RuntimeError("changed action observed")

            with mock.patch.object(engine, "_render_cached_page", side_effect=fail_render):
                with self.assertRaisesRegex(RuntimeError, "changed action observed"):
                    asyncio.run(
                        self.make_workflow_coordinator(engine, session).execute(
                            ProjectCommand(
                                project_id=project_id,
                                action="rerender",
                                config=config,
                            ),
                            progress=observe_changed_action,
                        )
                    )

            self.assertEqual(rerender_events[0], {"event": "start", "total_pages": 2})

    def test_translation_stage_builds_inpainted_base_from_detected_regions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_path = root / "page-1.png"
            page_cache = root / "cache" / "page-1.png"
            page_cache.mkdir(parents=True)
            Image.new("RGB", (48, 48), (255, 255, 255)).save(source_path)
            Image.new("RGB", (48, 48), (255, 255, 255)).save(page_cache / "inpainted.png")
            (page_cache / "meta.json").write_text(
                json.dumps({"base_kind": "source"}),
                encoding="utf-8",
            )
            region = SimpleNamespace(
                lines=[[[10, 10], [30, 10], [30, 30], [10, 30]]],
                xyxy=[10, 10, 30, 30],
                font_size=16,
                disabled_region=False,
            )
            captured: dict[str, object] = {}

            async def fake_inpaint(base_rgb, selection_mask, *, device):
                captured["mask_nonzero"] = int(np.count_nonzero(selection_mask))
                captured["device"] = device
                return np.zeros_like(base_rgb)

            engine._load_cached_regions = lambda _path: [region]  # type: ignore[method-assign]
            engine._run_local_lama_inpaint = fake_inpaint  # type: ignore[method-assign]

            asyncio.run(
                engine._ensure_translation_base_image(
                    source_path=source_path,
                    page_cache_dir=page_cache,
                    config={"use_gpu": False},
                )
            )

            self.assertGreater(captured["mask_nonzero"], 0)
            self.assertEqual(captured["device"], "cpu")
            self.assertEqual(
                json.loads((page_cache / "meta.json").read_text(encoding="utf-8"))["base_kind"],
                "inpainted",
            )
            self.assertEqual(
                np.asarray(Image.open(page_cache / "inpainted.png"))[0, 0].tolist(),
                [0, 0, 0],
            )

    def test_translation_base_does_not_clean_connected_white_art_outside_text_mask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_path = root / "page-1.png"
            page_cache = root / "cache" / "page-1.png"
            page_cache.mkdir(parents=True)

            source = np.full((600, 600, 3), 128, dtype=np.uint8)
            cv2.rectangle(source, (90, 90), (290, 340), (255, 255, 255), -1)
            cv2.line(source, (122, 132), (122, 210), (0, 0, 0), 2)
            cv2.line(source, (230, 150), (260, 250), (0, 0, 0), 3)
            Image.fromarray(source).save(source_path)
            Image.fromarray(source).save(page_cache / "inpainted.png")
            (page_cache / "meta.json").write_text(
                json.dumps({"base_kind": "source"}),
                encoding="utf-8",
            )

            region = SimpleNamespace(
                lines=[[[116, 128], [128, 128], [128, 214], [116, 214]]],
                xyxy=[116, 128, 128, 214],
                font_size=14,
                disabled_region=False,
            )

            async def fake_inpaint(base_rgb, selection_mask, *, device):
                edited = base_rgb.copy()
                edited[selection_mask > 0] = [248, 248, 248]
                return edited

            engine._load_cached_regions = lambda _path: [region]  # type: ignore[method-assign]
            engine._run_local_lama_inpaint = fake_inpaint  # type: ignore[method-assign]
            engine._select_local_inpainting_device = lambda _use_gpu: "cpu"  # type: ignore[method-assign]

            asyncio.run(
                engine._ensure_translation_base_image(
                    source_path=source_path,
                    page_cache_dir=page_cache,
                    config={"use_gpu": False, "mask_cleanup_strength": "standard"},
                )
            )

            output = np.asarray(Image.open(page_cache / "inpainted.png").convert("RGB"))
            self.assertGreater(int(output[170, 122, 0]), 240)
            self.assertLess(int(output[200, 245, 0]), 32)

    def test_translation_base_regenerates_bad_white_container_cleanup_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_path = root / "page-1.png"
            page_cache = root / "cache" / "page-1.png"
            page_cache.mkdir(parents=True)

            source = np.full((80, 80, 3), 255, dtype=np.uint8)
            source[20:44, 20:32] = [0, 0, 0]
            Image.fromarray(source).save(source_path)
            Image.fromarray(np.zeros_like(source)).save(page_cache / "inpainted.png")
            (page_cache / "meta.json").write_text(
                json.dumps({"base_kind": "inpainted", "white_container_cleanup_version": 1}),
                encoding="utf-8",
            )

            region = SimpleNamespace(
                lines=[[[20, 20], [32, 20], [32, 44], [20, 44]]],
                xyxy=[20, 20, 32, 44],
                font_size=12,
                disabled_region=False,
            )
            observed = {"called": False}

            async def fake_inpaint(base_rgb, selection_mask, *, device):
                observed["called"] = True
                edited = base_rgb.copy()
                edited[selection_mask > 0] = [248, 248, 248]
                return edited

            engine._load_cached_regions = lambda _path: [region]  # type: ignore[method-assign]
            engine._run_local_lama_inpaint = fake_inpaint  # type: ignore[method-assign]
            engine._select_local_inpainting_device = lambda _use_gpu: "cpu"  # type: ignore[method-assign]

            asyncio.run(
                engine._ensure_translation_base_image(
                    source_path=source_path,
                    page_cache_dir=page_cache,
                    config={"use_gpu": False, "mask_cleanup_strength": "standard"},
                )
            )

            output = np.asarray(Image.open(page_cache / "inpainted.png").convert("RGB"))
            meta = json.loads((page_cache / "meta.json").read_text(encoding="utf-8"))
            self.assertTrue(observed["called"])
            self.assertGreater(int(output[30, 26, 0]), 240)
            self.assertEqual(meta["base_kind"], "inpainted")
            self.assertNotIn("white_container_cleanup_version", meta)

    def test_cpu_inpainting_device_does_not_import_pytorch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            with mock.patch.dict(sys.modules, {"torch": None}):
                self.assertEqual(
                    engine._select_local_inpainting_device(False),
                    "cpu",
                )

    def test_inference_device_uses_mps_when_cuda_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            fake_torch = SimpleNamespace(
                cuda=SimpleNamespace(is_available=lambda: False),
                backends=SimpleNamespace(
                    mps=SimpleNamespace(is_available=lambda: True),
                ),
            )
            with mock.patch.dict(sys.modules, {"torch": fake_torch}):
                self.assertEqual(engine._select_inference_device(True), "mps")

    def test_successful_detect_atomically_commits_staged_outputs_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-a"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            Image.new("RGB", (16, 16), (200, 0, 0)).save(output_dir / "page-1.png")
            live_cache = engine._prepare_rerender_cache_dir(project_id, reset=True)
            old_cache_page = live_cache / "page-1.png"
            old_cache_page.mkdir()
            (old_cache_page / "regions.json").write_text("[]", encoding="utf-8")
            Image.new("RGB", (16, 16), (200, 0, 0)).save(old_cache_page / "inpainted.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "download_path": None,
                "translated_output_map": {"page-1.png": "page-1.png"},
                "workflow_stage": "translated",
                "rerender_cache_dir": str(live_cache),
                "manual_regions": {},
            }
            engine.initialize_project(project_id, session, title="Existing project")

            async def fake_command(**kwargs):
                staged_session = kwargs["session"]
                staged_output = Path(staged_session["translated_dir"])
                staged_cache_page = Path(staged_session["rerender_cache_dir"]) / "page-1.png"
                staged_output.mkdir(parents=True, exist_ok=True)
                staged_cache_page.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (16, 16), (0, 200, 0)).save(staged_output / "page-1.png")
                Image.new("RGB", (16, 16), (0, 200, 0)).save(staged_cache_page / "inpainted.png")
                (staged_cache_page / "regions.json").write_text("[]", encoding="utf-8")
                (staged_cache_page / "meta.json").write_text(
                    json.dumps({"base_kind": "source"}),
                    encoding="utf-8",
                )
                staged_session["translated_output_map"] = {"page-1.png": "page-1.png"}
                return 0

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._run_translation_command = fake_command  # type: ignore[method-assign]
            ensured_base_images: list[str] = []

            async def fake_ensure_base_image(*, source_path, page_cache_dir, config):
                ensured_base_images.append(Path(source_path).name)
                Image.new("RGB", (16, 16), (240, 240, 240)).save(Path(page_cache_dir) / "inpainted.png")
                meta_path = Path(page_cache_dir) / "meta.json"
                meta_path.write_text(json.dumps({"base_kind": "inpainted"}), encoding="utf-8")

            engine._ensure_translation_base_image = fake_ensure_base_image  # type: ignore[method-assign]

            async def progress(_event):
                return None

            result = asyncio.run(
                self.make_workflow_coordinator(engine, session).execute(
                    ProjectCommand(
                        project_id=project_id,
                        action="detect",
                        config={"translator": "none"},
                    ),
                    progress=progress,
                )
            )

            self.assertEqual(result["translated_dir"], str(output_dir.resolve()))
            self.assertEqual(session["translated_dir"], str(output_dir))
            self.assertEqual(session["rerender_cache_dir"], str(live_cache))
            self.assertEqual(ensured_base_images, ["page-1.png"])
            self.assertEqual(session["workflow_stage"], "detected")
            self.assertEqual(session["translated_output_map"], {})
            self.assertFalse((output_dir / "page-1.png").exists())
            self.assertEqual(
                np.asarray(Image.open(live_cache / "page-1.png" / "inpainted.png"))[0, 0].tolist(),
                [240, 240, 240],
            )
            page_artifact = engine.build_client_session_payload(
                project_id,
                session,
            )["page_artifacts"]["page-1.png"]
            self.assertTrue(page_artifact["capabilities"]["recognition_ready"])
            self.assertTrue(page_artifact["capabilities"]["blank_ready"])
            self.assertFalse(page_artifact["capabilities"]["translation_ready"])
            self.assertFalse(page_artifact["capabilities"]["final_ready"])

    def test_project_storage_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            sentinel = engine.paths.app_data_dir / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")

            for invalid_project_id in ("", ".", "..", "../outside", "nested/project", "nested\\project", "\x00"):
                with self.subTest(project_id=repr(invalid_project_id)):
                    with self.assertRaises(InvalidStorageIdentifierError):
                        engine.delete_project(invalid_project_id)

            self.assertTrue(sentinel.exists())
            self.assertEqual(
                engine._project_dir("legacy.project_1-test"),
                engine.projects_root.resolve() / "legacy.project_1-test",
            )

    def test_page_storage_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            for invalid_page_id in ("", ".", "..", "../page.png", "nested/page.png", "nested\\page.png", "\x00"):
                with self.subTest(page_id=repr(invalid_page_id)):
                    with self.assertRaises(InvalidStorageIdentifierError):
                        engine._project_page_document_path("project-a", invalid_page_id)

            self.assertEqual(
                engine._project_page_document_path("project-a", "0001.png"),
                engine.projects_root.resolve() / "project-a" / "pages" / "0001.png" / "page_document.json",
            )

    def test_stroke_strength_accepts_values_above_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            self.assertEqual(engine._normalize_stroke_strength(3.25), 3.25)
            self.assertEqual(engine._normalize_stroke_strength(99), 5.0)

    def test_duplicate_region_copies_style_and_offsets_bbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "duplicate-project"
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            Image.new("RGB", (400, 600), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {"target_lang": "CHS"},
                "manual_regions": {},
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "dimensions": {"width": 400, "height": 600},
                "regions": [{
                    "region_id": "source-region",
                    "bbox": [40, 50, 180, 240],
                    "direction": "v",
                    "source_text": "原文",
                    "translation": {"machine": "译文", "edited": "新译文", "resolved": "新译文"},
                    "style": {
                        "font_style": "handwritten",
                        "font_key_override": "project:test.ttf",
                        "font_size": 36,
                        "letter_spacing": 1.2,
                        "line_spacing": 1.3,
                        "alignment": "left",
                        "fg_color": [12, 34, 56],
                        "bg_color": [240, 241, 242],
                        "stroke_width": 2.5,
                        "rotation": 8,
                    },
                    "flags": {
                        "disabled": True,
                        "keep_original": True,
                        "preserve_background": True,
                    },
                }],
            })

            duplicated = engine.duplicate_region(
                project_id=project_id,
                session=session,
                stored_name="page-1.png",
                region_id="source-region",
                raw_config={"target_lang": "CHS"},
            )

            duplicated_id = duplicated["id"]
            self.assertNotEqual(duplicated["bbox"], [40, 50, 180, 240])
            self.assertEqual(duplicated["font_size"], 30)
            self.assertEqual(duplicated["stroke_width"], 2.5)
            self.assertEqual(session["translation_region_overrides"][duplicated_id], "新译文")
            self.assertEqual(session["translation_region_layout_overrides"][duplicated_id]["font_key"], "project:test.ttf")
            self.assertEqual(session["style_region_overrides"][duplicated_id], "handwritten")
            self.assertTrue(session["translation_region_disabled_overrides"][duplicated_id])
            self.assertTrue(session["translation_region_skip_overrides"][duplicated_id])
            original_region = SimpleNamespace(
                xyxy=[40, 50, 180, 240],
                translation="新译文",
                text="原文",
                font_size=36,
                manual_region=False,
                allow_overlap=False,
            )
            duplicated_region = SimpleNamespace(
                xyxy=duplicated["bbox"],
                translation="新译文",
                text="原文",
                font_size=36,
                manual_region=True,
                allow_overlap=True,
            )
            self.assertEqual(len(engine._dedupe_overlapping_regions([original_region, duplicated_region])), 2)

    def test_export_archives_use_project_result_and_blank_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            Image.new("RGB", (20, 20), (255, 255, 255)).save(source_dir / "001.jpg")
            Image.new("RGB", (20, 20), (245, 245, 245)).save(source_dir / "002.png")
            Image.new("RGB", (20, 20), (0, 0, 0)).save(translated_dir / "001.png")
            Image.new("RGB", (20, 20), (10, 10, 10)).save(translated_dir / "002.png")
            session = {
                "project_title": "项目:测试",
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [
                    {"name": "001.jpg", "stored_name": "001.jpg"},
                    {"name": "002.png", "stored_name": "002.png"},
                ],
                "translated_output_map": {
                    "001.jpg": "001.png",
                    "002.png": "002.png",
                },
                "last_config": {"rerender_output_format": "png"},
            }

            result_archive = engine.build_session_archive("project-a", session)
            blank_archive = engine.build_blank_session_archive("project-a", session)

            with zipfile.ZipFile(result_archive) as archive:
                self.assertEqual(archive.namelist(), [
                    "项目_测试_result_0001.png",
                    "项目_测试_result_0002.png",
                ])
                self.assertTrue(all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist()))
            with zipfile.ZipFile(blank_archive) as archive:
                self.assertEqual(archive.namelist(), [
                    "项目_测试_blank_0001.png",
                    "项目_测试_blank_0002.png",
                ])
                self.assertTrue(all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist()))
            self.assertEqual(engine.get_export_archive_filename("project-a", session, "result"), "项目_测试_result.zip")
            self.assertEqual(engine.get_export_archive_filename("project-a", session, "blank"), "项目_测试_blank.zip")

    def test_project_summary_and_payload_include_persisted_region_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            Image.new("RGB", (20, 20), (255, 255, 255)).save(source_dir / "001.png")
            Image.new("RGB", (20, 20), (245, 245, 245)).save(source_dir / "002.png")
            session = {
                "project_title": "框数项目",
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [
                    {"name": "001.png", "stored_name": "001.png"},
                    {"name": "002.png", "stored_name": "002.png"},
                ],
                "translated_output_map": {},
                "workflow_stage": "detected",
                "last_config": {"rerender_output_format": "png"},
            }
            engine._write_json_file(engine._project_page_document_path("project-a", "001.png"), {
                "regions": [{"id": "a"}, {"id": "b"}],
            })
            engine._write_json_file(engine._project_page_document_path("project-a", "002.png"), {
                "regions": [{"id": "c"}],
            })

            summary = engine._build_project_summary("project-a", session)
            payload = engine.build_client_session_payload("project-a", session)

            self.assertEqual(summary["region_count"], 3)
            self.assertEqual(payload["project"]["region_count"], 3)
            self.assertEqual([image["region_count"] for image in payload["images"]], [2, 1])

    def test_build_session_archive_rejects_missing_translated_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            Image.new("RGB", (20, 20), (255, 255, 255)).save(source_dir / "001.png")
            Image.new("RGB", (20, 20), (245, 245, 245)).save(source_dir / "002.png")
            Image.new("RGB", (20, 20), (0, 0, 0)).save(translated_dir / "001.png")
            session = {
                "project_title": "缺页项目",
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [
                    {"name": "001.png", "stored_name": "001.png"},
                    {"name": "002.png", "stored_name": "002.png"},
                ],
                "translated_output_map": {"001.png": "001.png"},
                "last_config": {"rerender_output_format": "png"},
            }

            with self.assertRaisesRegex(RuntimeError, "缺少翻译结果"):
                engine.build_session_archive("project-a", session)

            self.assertFalse((translated_dir / "002.png").exists())

    def test_project_glossary_extraction_uses_large_context_and_longer_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "project_glossary": {"entries": []},
            }
            very_long_text = "山田 小夏 " * 5000
            engine._project_text_context_for_glossary = lambda *_args, **_kwargs: very_long_text  # type: ignore[method-assign]
            captured: dict[str, object] = {}

            def fake_completion(**kwargs) -> str:
                captured.update(kwargs)
                return '[{"source":"山田","translation":"山田","category":"人名"}]'

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._request_chat_completions_text_sync = fake_completion  # type: ignore[method-assign]

            glossary = asyncio.run(engine.extract_project_glossary("project-a", session, {
                "translator": "custom_openai",
                "selected_translator": "openai-compatible",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "model",
                "api_key": "key",
                "target_lang": "CHS",
            }, force=True))

            self.assertEqual(glossary["entries"][0]["source"], "山田")
            self.assertGreater(len(str(captured["user_prompt"])), 24000)
            self.assertLessEqual(len(str(captured["user_prompt"])), engine.PROJECT_GLOSSARY_PROMPT_CHAR_LIMIT)
            self.assertEqual(captured["timeout_seconds"], engine.PROJECT_GLOSSARY_REQUEST_TIMEOUT_SECONDS)

    def test_project_glossary_extraction_only_shrinks_context_after_provider_limit_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "project_glossary": {"entries": []},
            }
            engine._project_text_context_for_glossary = (  # type: ignore[method-assign]
                lambda *_args, **_kwargs: "普通上下文 " * 10000
            )
            prompts: list[str] = []

            async def fake_completion(_config, prompt):
                prompts.append(prompt)
                if len(prompts) == 1:
                    raise RuntimeError("maximum context length exceeded")
                return '[{"source":"山田","translation":"山田","category":"人名"}]'

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._request_project_glossary_extraction = fake_completion  # type: ignore[method-assign]
            glossary = asyncio.run(engine.extract_project_glossary("project-a", session, {
                "translator": "custom_openai",
                "selected_translator": "openai-compatible",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "small-context-model",
                "api_key": "key",
                "target_lang": "CHS",
            }, force=True))

            self.assertEqual(glossary["entries"][0]["source"], "山田")
            self.assertEqual(len(prompts), 2)
            self.assertGreater(len(prompts[0]), len(prompts[1]))
            self.assertLessEqual(len(prompts[1]), engine.PROJECT_GLOSSARY_FALLBACK_PROMPT_CHAR_LIMIT)

    def test_brush_edit_operations_paint_restore_and_erase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            base = np.full((100, 100, 3), 240, dtype=np.uint8)
            source = np.full((100, 100, 3), (20, 40, 180), dtype=np.uint8)
            operations = engine._normalize_brush_edit_operations(
                [
                    {
                        "mode": "paint",
                        "color": [255, 0, 0],
                        "size": 18,
                        "points": [[0.2, 0.2]],
                    },
                    {
                        "mode": "restore",
                        "size": 18,
                        "points": [[0.5, 0.5]],
                    },
                    {
                        "mode": "paint",
                        "color": [0, 0, 0],
                        "size": 18,
                        "points": [[0.8, 0.8]],
                    },
                    {
                        "mode": "erase",
                        "size": 18,
                        "points": [[0.8, 0.8]],
                    },
                ],
                base.shape,
            )

            edited = engine._apply_brush_edit_operations(base, source, operations)

            np.testing.assert_array_equal(edited[20, 20], np.array([255, 0, 0], dtype=np.uint8))
            np.testing.assert_array_equal(edited[50, 50], source[50, 50])
            np.testing.assert_array_equal(edited[80, 80], base[80, 80])

    def test_delete_project_removes_project_storage_and_preview_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            project_id = "project-delete"
            paths_to_remove = [
                engine._project_dir(project_id),
                engine._project_output_dir(project_id),
                engine._rerender_cache_dir(project_id),
                engine._mask_debug_dir(project_id),
                engine._style_rerender_debug_dir(project_id),
                engine._image_preview_project_cache_dir(project_id),
            ]
            for path in paths_to_remove:
                path.mkdir(parents=True, exist_ok=True)
                (path / "marker.txt").write_text("x", encoding="utf-8")
            (engine.temp_dir / f"{project_id}_detect.log").write_text("log", encoding="utf-8")
            engine._write_project_index([
                {"project_id": project_id, "title": "delete me"},
                {"project_id": "keep-project", "title": "keep me"},
            ])

            engine.delete_project(project_id)

            for path in paths_to_remove:
                self.assertFalse(path.exists(), str(path))
            self.assertFalse((engine.temp_dir / f"{project_id}_detect.log").exists())
            remaining = engine._read_json_file(engine.project_index_path, [])
            self.assertEqual([item["project_id"] for item in remaining], ["keep-project"])

    def test_snapshot_restore_keeps_an_explicitly_empty_user_region_collection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "snapshot-empty-regions"
            page_id = "0001.png"
            source_dir = engine._project_source_dir(project_id)
            translated_dir = engine._project_translated_dir(project_id)
            source_dir.mkdir(parents=True)
            translated_dir.mkdir(parents=True)
            Image.new("RGB", (32, 32), (255, 255, 255)).save(source_dir / page_id)
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "Page 1", "stored_name": page_id}],
                "translated_output_map": {},
                "workflow_stage": "idle",
                "manual_regions": {},
            }
            engine.initialize_project(project_id, session, title="Snapshot empty regions")
            engine.persist_project_state(
                project_id,
                session,
                snapshot_kind="before_user_regions",
                snapshot_summary="No user regions",
            )
            snapshot_id = engine.list_project_snapshots(project_id)[0]["snapshot_id"]

            session["manual_regions"] = {
                page_id: [
                    {
                        "id": f"manual::{page_id}::later",
                        "stored_name": page_id,
                        "bbox": [4, 4, 20, 20],
                        "source_text": "later",
                        "translation": "后来新增",
                    }
                ]
            }
            engine.persist_project_state(project_id, session)

            _restored_project_id, restored_session = engine.restore_snapshot_as_project(
                project_id,
                snapshot_id,
            )

            self.assertEqual(restored_session["manual_regions"], {})

    def test_persist_project_state_delegates_pending_cleanup_to_atomic_workspace_seam(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "atomic-persist-cleanup"
            session = {
                "source_dir": str(root / "source"),
                "translated_dir": str(root / "translated"),
                "source_images": [],
                "translated_output_map": {},
                "workflow_stage": "idle",
            }
            engine.initialize_project(project_id, session, title="Atomic cleanup")

            with (
                mock.patch.object(
                    engine.project_workspace,
                    "clear_obsolete_pending_artifact_set",
                    return_value=False,
                ) as clear_obsolete,
                mock.patch.object(
                    engine.project_workspace,
                    "read_pending_artifact_set",
                    side_effect=AssertionError(
                        "Translator must not make a lock-free Pending decision"
                    ),
                ),
                mock.patch.object(
                    engine.project_workspace,
                    "clear_pending_artifact_set",
                    side_effect=AssertionError(
                        "Translator must not unconditionally clear Pending"
                    ),
                ),
                mock.patch.object(
                    engine.project_workspace,
                    "garbage_collect_snapshot_blobs",
                ) as collect,
            ):
                engine.persist_project_state(project_id, session)

            clear_obsolete.assert_called_once_with(project_id)
            collect.assert_called_once_with(project_id)

    def test_snapshot_restore_uses_snapshot_time_artifacts_instead_of_current_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "snapshot-artifact-history"
            page_id = "0001.png"
            source_dir = engine._project_source_dir(project_id)
            translated_dir = engine._project_translated_dir(project_id)
            cache_dir = engine._rerender_cache_dir(project_id)
            page_dir = engine._project_page_dir(project_id, page_id)
            source_dir.mkdir(parents=True)
            translated_dir.mkdir(parents=True)
            (cache_dir / page_id).mkdir(parents=True)
            page_dir.mkdir(parents=True)
            Image.new("RGB", (32, 32), (10, 20, 30)).save(source_dir / page_id)
            Image.new("RGB", (32, 32), (40, 50, 60)).save(translated_dir / page_id)
            (cache_dir / page_id / "snapshot-marker.txt").write_text("old-cache", encoding="utf-8")
            (page_dir / "snapshot-marker.txt").write_text("old-page", encoding="utf-8")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "rerender_cache_dir": str(cache_dir),
                "source_images": [{"name": "Page 1", "stored_name": page_id}],
                "translated_output_map": {page_id: page_id},
                "workflow_stage": "translated",
                "manual_regions": {},
            }
            engine.initialize_project(project_id, session, title="Snapshot artifacts")
            engine.persist_project_state(
                project_id,
                session,
                snapshot_kind="historical_artifacts",
                snapshot_summary="Capture old files",
                persist_page_documents=True,
            )
            snapshot_id = engine.list_project_snapshots(project_id)[0]["snapshot_id"]

            Image.new("RGB", (32, 32), (110, 120, 130)).save(source_dir / page_id)
            Image.new("RGB", (32, 32), (140, 150, 160)).save(translated_dir / page_id)
            (cache_dir / page_id / "snapshot-marker.txt").write_text("new-cache", encoding="utf-8")
            (page_dir / "snapshot-marker.txt").write_text("new-page", encoding="utf-8")

            restored_project_id, restored_session = engine.restore_snapshot_as_project(
                project_id,
                snapshot_id,
            )

            restored_source = np.asarray(Image.open(Path(restored_session["source_dir"]) / page_id))
            restored_output = np.asarray(Image.open(Path(restored_session["translated_dir"]) / page_id))
            restored_cache_dir = engine._rerender_cache_dir(restored_project_id)
            restored_page_dir = engine._project_page_dir(restored_project_id, page_id)
            self.assertEqual(restored_source[0, 0].tolist(), [10, 20, 30])
            self.assertEqual(restored_output[0, 0].tolist(), [40, 50, 60])
            self.assertEqual(
                (restored_cache_dir / page_id / "snapshot-marker.txt").read_text(encoding="utf-8"),
                "old-cache",
            )
            self.assertEqual(
                (restored_page_dir / "snapshot-marker.txt").read_text(encoding="utf-8"),
                "old-page",
            )

    def test_page_commands_reject_unknown_region_without_dirty_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            session = {
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "translation_region_overrides": {},
                "translation_region_layout_overrides": {},
                "last_config": {},
            }
            engine._page_document_region_ids = lambda *_args, **_kwargs: {"known-region"}  # type: ignore[method-assign]

            with self.assertRaises(FileNotFoundError):
                asyncio.run(engine.apply_page_commands(
                    project_id="project-a",
                    session=session,
                    page_id="page-1.png",
                    raw_config={},
                    commands=[{
                        "type": "update_translation",
                        "region_id": "missing-region",
                        "text": "should not persist",
                    }],
                ))

            self.assertNotIn("missing-region", session["translation_region_overrides"])
            self.assertNotIn("missing-region", session["translation_region_layout_overrides"])

    def test_page_command_rollback_preserves_a_concurrently_published_snapshot(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            workspace = engine.project_workspace
            project_id = "concurrent-snapshot-rollback"
            page_id = "001.png"
            state_document = {
                "schema_version": 2,
                "project_id": project_id,
                "source_images": [{"name": page_id, "stored_name": page_id}],
            }
            project_manifest = {
                "project_id": project_id,
                "title": "Concurrent snapshot rollback",
            }
            first_head = workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest=project_manifest,
                page_documents={
                    page_id: {
                        "page_id": page_id,
                        "metadata": {"revision": 1},
                    }
                },
            )
            workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest=project_manifest,
                page_documents={
                    page_id: {
                        "page_id": page_id,
                        "metadata": {"revision": 2},
                    }
                },
                expected_generation=first_head["generation"],
                expected_revision_id=first_head["revision_id"],
            )
            published_snapshots: list[dict[str, object]] = []

            async def publish_snapshot_then_fail(**_kwargs) -> dict[str, object]:
                snapshot = await asyncio.to_thread(
                    workspace.create_project_head_snapshot,
                    project_id,
                    first_head,
                    {
                        "kind": "manual",
                        "summary": "Concurrent durable snapshot",
                        "created_at": "2026-07-19T07:00:00+00:00",
                    },
                )
                published_snapshots.append(snapshot)
                raise RuntimeError("synthetic page-command failure")

            session: dict[str, object] = {
                "source_images": [{"name": page_id, "stored_name": page_id}],
                "translation_region_overrides": {},
                "translation_region_layout_overrides": {},
                "last_config": {},
            }
            with mock.patch.object(
                engine,
                "_apply_page_commands_once",
                side_effect=publish_snapshot_then_fail,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "synthetic page-command failure",
                ):
                    asyncio.run(
                        engine.apply_page_commands(
                            project_id=project_id,
                            session=session,
                            page_id=page_id,
                            raw_config={},
                            commands=[{"type": "rerender_page"}],
                        )
                    )

            self.assertEqual(len(published_snapshots), 1)
            snapshot = published_snapshots[0]
            restored_pages = root / "restored-pages"
            workspace.restore_snapshot_artifacts(
                project_id,
                snapshot["artifact_bundle"],
                {"pages": restored_pages},
            )
            restored_page_document = json.loads(
                (restored_pages / page_id / "page_document.json").read_text(
                    encoding="utf-8"
                )
            )
            first_page_blob = first_head["files"][
                f"pages/{page_id}/page_document.json"
            ]["blob"]
            self.assertEqual(restored_page_document["metadata"]["revision"], 1)
            self.assertEqual(len(workspace.read_snapshot_manifests(project_id)), 1)
            self.assertTrue(
                (
                    workspace.project_revisions_dir(project_id)
                    / f"{first_head['revision_id']}.json"
                ).is_file()
            )
            self.assertTrue(
                (
                    workspace.project_artifact_store_dir(project_id)
                    / first_page_blob[:2]
                    / first_page_blob
                ).is_file()
            )

    def test_translation_override_preserves_single_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            session = {"translation_region_overrides": {}}

            engine._set_region_translation_override_value(session, "region-1", " ")
            self.assertEqual(session["translation_region_overrides"]["region-1"], " ")

            normalized = engine._normalize_translation_region_overrides({"region-1": " "})
            self.assertEqual(normalized, {"region-1": " "})

            engine._set_region_translation_override_value(session, "region-1", "")
            self.assertNotIn("region-1", session["translation_region_overrides"])

    def test_restore_rejects_project_when_all_source_images_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            project_id = "broken-project"
            state = {
                "project_id": project_id,
                "source_dir": str(Path(tmp) / "missing-source"),
                "translated_dir": str(Path(tmp) / "missing-translated"),
                "source_images": [{"name": "gone.png", "stored_name": "gone.png"}],
                "workflow_stage": "translated",
                "translated_output_map": {"gone.png": "gone.png"},
            }
            engine._write_json_file(engine._project_session_state_path(project_id), state)
            engine._write_json_file(engine._project_manifest_path(project_id), {"project_id": project_id})

            with self.assertRaises(FileNotFoundError):
                engine.restore_project_session(project_id)

    def test_restore_recovers_rerender_variant_outputs_and_translated_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            project_id = "rerender-restore"
            source_dir = engine._project_source_dir(project_id)
            translated_dir = engine._project_translated_dir(project_id)
            source_dir.mkdir(parents=True)
            translated_dir.mkdir(parents=True)
            Image.new("RGB", (8, 8), (255, 255, 255)).save(source_dir / "page-1.png")
            Image.new("RGB", (8, 8), (240, 240, 240)).save(source_dir / "page-2.png")
            Image.new("RGB", (8, 8), (20, 20, 20)).save(translated_dir / "page-1.png")
            Image.new("RGB", (8, 8), (30, 30, 30)).save(translated_dir / "page-1__rerender-2.png")
            Image.new("RGB", (8, 8), (40, 40, 40)).save(translated_dir / "page-2__rerender-2.png")
            os.utime(translated_dir / "page-1.png", (1, 1))
            os.utime(translated_dir / "page-1__rerender-2.png", (2, 2))
            os.utime(translated_dir / "page-2__rerender-2.png", (2, 2))
            state = {
                "project_id": project_id,
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [
                    {"name": "page-1.png", "stored_name": "page-1.png"},
                    {"name": "page-2.png", "stored_name": "page-2.png"},
                ],
                "translated_output_map": {},
                "workflow_stage": "detected",
                "last_config": {"rerender_output_format": "png"},
            }
            engine._write_json_file(engine._project_session_state_path(project_id), state)
            engine._write_json_file(engine._project_manifest_path(project_id), {"project_id": project_id})

            session = engine.restore_project_session(project_id)
            payload = engine.build_client_session_payload(project_id, session)

            self.assertEqual(session["workflow_stage"], "translated")
            self.assertEqual(session["translated_output_map"]["page-1.png"], "page-1__rerender-2.png")
            self.assertEqual(session["translated_output_map"]["page-2.png"], "page-2__rerender-2.png")
            self.assertEqual(payload["workflow_stage"], "translated")
            self.assertEqual(len(payload["translated_images"]), 2)

    def test_restore_recovers_detected_stage_from_persisted_page_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            project_id = "detected-restore"
            source_dir = engine._project_source_dir(project_id)
            translated_dir = engine._project_translated_dir(project_id)
            source_dir.mkdir(parents=True)
            translated_dir.mkdir(parents=True)
            Image.new("RGB", (8, 8), (255, 255, 255)).save(source_dir / "page-1.png")
            state = {
                "project_id": project_id,
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "translated_output_map": {},
                "workflow_stage": "idle",
                "last_config": {},
            }
            engine._write_json_file(engine._project_session_state_path(project_id), state)
            engine._write_json_file(engine._project_manifest_path(project_id), {"project_id": project_id})
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "regions": [{
                    "region_id": "region-1",
                    "bbox": [1, 1, 4, 4],
                    "source_text": "こんにちは",
                    "translation": {"machine": "", "resolved": "こんにちは"},
                }],
            })

            session = engine.restore_project_session(project_id)

            self.assertEqual(session["workflow_stage"], "detected")

    def test_editable_cache_repairs_unreadable_base_from_traditional_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            project_id = "cache-repair"
            source_dir = engine._project_source_dir(project_id)
            source_dir.mkdir(parents=True)
            source_path = source_dir / "page-1.png"
            Image.new("RGB", (8, 8), (255, 255, 255)).save(source_path)
            session = {
                "source_dir": str(source_dir),
                "rerender_cache_dir": str(Path(tmp) / "rerender-cache"),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
            }
            cache_dir = engine._session_page_cache_dir(session, project_id, "page-1.png")
            cache_dir.mkdir(parents=True)
            (cache_dir / "regions.json").write_text("[]", encoding="utf-8")
            (cache_dir / "inpainted.png").write_bytes(b"not-a-png")
            backup_path = engine._advanced_erase_traditional_backup_path(cache_dir)
            backup_path.parent.mkdir(parents=True)
            backup_bgr = np.zeros((8, 8, 3), dtype=np.uint8)
            backup_bgr[:, :] = [12, 34, 56]
            cv2.imwrite(str(backup_path), backup_bgr)

            repaired = engine._ensure_editable_page_cache(
                session_id=project_id,
                session=session,
                stored_name="page-1.png",
                config={},
                source_path=source_path,
            )
            repaired_bgr = cv2.imread(str(cache_dir / "inpainted.png"), cv2.IMREAD_COLOR)

            self.assertTrue(repaired)
            self.assertIsNotNone(repaired_bgr)
            self.assertEqual(repaired_bgr[0, 0].tolist(), [12, 34, 56])
            self.assertTrue(any(path.name.startswith("inpainted.corrupt-") for path in cache_dir.iterdir()))

    def test_editable_cache_allows_empty_region_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            project_id = "empty-region-cache"
            source_dir = engine._project_source_dir(project_id)
            source_dir.mkdir(parents=True)
            source_path = source_dir / "0200.jpg"
            Image.new("RGB", (8, 8), (255, 255, 255)).save(source_path)
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(engine._project_translated_dir(project_id)),
                "rerender_cache_dir": str(Path(tmp) / "rerender-cache"),
                "source_images": [{"name": "0200.jpg", "stored_name": "0200.jpg"}],
                "manual_regions": {},
                "last_config": {},
            }

            restored = engine._ensure_editable_page_cache(
                session_id=project_id,
                session=session,
                stored_name="0200.jpg",
                config={},
                source_path=source_path,
            )
            cache_dir = engine._session_page_cache_dir(session, project_id, "0200.jpg")
            regions = json.loads((cache_dir / "regions.json").read_text(encoding="utf-8"))

            self.assertTrue(restored)
            self.assertEqual(regions, [])
            self.assertTrue((cache_dir / "inpainted.png").exists())

    def test_openai_compatible_settings_validation_uses_lightweight_http_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            async def fail_vendor_translation(*_args, **_kwargs):
                raise AssertionError("settings validation should not import the full translator dispatcher")

            def fake_validation_request(**kwargs):
                self.assertEqual(kwargs["base_url"], "https://api.example.com/v1")
                self.assertEqual(kwargs["model"], "example-model")
                self.assertEqual(kwargs["api_key"], "secret")
                return "测试"

            engine._translate_text_batch = fail_vendor_translation  # type: ignore[method-assign]
            engine._request_chat_completions_validation_sync = fake_validation_request  # type: ignore[method-assign]

            result = asyncio.run(engine.validate_user_config({
                "translator": "openai-compatible",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "example-model",
                "api_key": "secret",
            }))

            self.assertTrue(result.get("ok"))
            self.assertEqual(result.get("preview"), "测试")
            self.assertEqual(result.get("translator"), "openai-compatible")

    def test_settings_validation_treats_empty_preview_as_successful_connection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            def fake_validation_request(**_kwargs):
                return ""

            engine._request_chat_completions_validation_sync = fake_validation_request  # type: ignore[method-assign]

            result = asyncio.run(engine.validate_user_config({
                "translator": "openai-compatible",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "example-model",
                "api_key": "secret",
            }))

            self.assertTrue(result.get("ok"))
            self.assertEqual(result.get("message"), "连接成功")
            self.assertEqual(result.get("preview"), "")

    def test_openai_compatible_settings_validation_requires_base_url_and_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            missing_base_url = asyncio.run(engine.validate_user_config({
                "translator": "openai-compatible",
                "openai_model": "example-model",
                "api_key": "secret",
            }))
            self.assertFalse(missing_base_url.get("ok"))
            self.assertIn("API Base URL", str(missing_base_url.get("message")))

            missing_model = asyncio.run(engine.validate_user_config({
                "translator": "openai-compatible",
                "openai_base_url": "https://api.example.com/v1",
                "api_key": "secret",
            }))
            self.assertFalse(missing_model.get("ok"))
            self.assertIn("模型名称", str(missing_model.get("message")))

    def test_openai_compatible_validation_request_uses_app_user_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            captured = {}

            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self):
                    return json.dumps({
                        "choices": [{
                            "message": {
                                "content": "测试"
                            }
                        }]
                    }).encode("utf-8")

            def fake_urlopen(request, timeout=0):
                captured["url"] = request.full_url
                captured["timeout"] = timeout
                captured["headers"] = dict(request.header_items())
                return FakeResponse()

            with mock.patch.object(translator_module.urllib_request, "urlopen", fake_urlopen):
                result = engine._request_chat_completions_validation_sync(
                    provider_label="OpenAI Compatible",
                    base_url="https://api.example.com/v1/chat/completions",
                    model="example-model",
                    api_key="secret",
                )

            self.assertEqual(result, "测试")
            self.assertEqual(captured["url"], "https://api.example.com/v1/chat/completions")
            self.assertEqual(captured["timeout"], 30)
            self.assertIn("Solar-Manga-Translator", captured["headers"].get("User-agent", ""))
            self.assertEqual(captured["headers"].get("Accept"), "application/json")

    def test_rerender_imports_avoid_vendor_utils_aggregate(self) -> None:
        render_files = [
            BACKEND_DIR / "patched_manga_translator_init.py",
            BACKEND_DIR / "patched_utils_init.py",
            BACKEND_DIR / "patched_inpainting_init.py",
            BACKEND_DIR / "patched_rendering_init.py",
            BACKEND_DIR / "patched_text_render.py",
        ]

        for render_file in render_files:
            with self.subTest(render_file=render_file.name):
                content = render_file.read_text(encoding="utf-8")
                self.assertNotIn("from ..utils import", content)

    def test_rendering_import_does_not_load_inference_stack(self) -> None:
        vendor_root = BACKEND_DIR / "manga-image-translator" / "manga_translator"
        if not vendor_root.exists():
            self.skipTest("manga-image-translator vendor checkout is not installed")

        script = """
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path("backend/manga-image-translator").resolve()))
sys.path.insert(0, str(Path("backend").resolve()))
from patch_pydensecrf import patch_mask_refinement

if not patch_mask_refinement():
    raise SystemExit("runtime patch failed")

import manga_translator.rendering

print(json.dumps({
    "onnxruntime": "onnxruntime" in sys.modules,
    "torch": "torch" in sys.modules,
    "utils_inference": "manga_translator.utils.inference" in sys.modules,
    "full_translator": "manga_translator.manga_translator" in sys.modules,
}, sort_keys=True))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(BACKEND_DIR.parent),
            text=True,
            capture_output=True,
            check=True,
        )
        loaded_modules = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(
            loaded_modules,
            {
                "onnxruntime": False,
                "torch": False,
                "utils_inference": False,
                "full_translator": False,
            },
        )

    def test_engine_import_does_not_load_onnxruntime(self) -> None:
        vendor_root = BACKEND_DIR / "manga-image-translator" / "manga_translator"
        if not vendor_root.exists():
            self.skipTest("manga-image-translator vendor checkout is not installed")

        script = """
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path("backend/manga-image-translator").resolve()))
sys.path.insert(0, str(Path("backend").resolve()))
from patch_pydensecrf import patch_mask_refinement

if not patch_mask_refinement():
    raise SystemExit("runtime patch failed")

import manga_translator.manga_translator

print(json.dumps({
    "onnxruntime": "onnxruntime" in sys.modules,
    "booru_tagger": "manga_translator.inpainting.booru_tagger" in sys.modules,
    "sd_inpainter": "manga_translator.inpainting.inpainting_sd" in sys.modules,
}, sort_keys=True))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(BACKEND_DIR.parent),
            text=True,
            capture_output=True,
            check=True,
        )
        loaded_modules = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(
            loaded_modules,
            {
                "onnxruntime": False,
                "booru_tagger": False,
                "sd_inpainter": False,
            },
        )

    def test_cli_args_import_does_not_trigger_runtime_cycle_or_onnxruntime(self) -> None:
        vendor_root = BACKEND_DIR / "manga-image-translator" / "manga_translator"
        if not vendor_root.exists():
            self.skipTest("manga-image-translator vendor checkout is not installed")

        script = """
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path("backend/manga-image-translator").resolve()))
sys.path.insert(0, str(Path("backend").resolve()))
from patch_pydensecrf import patch_mask_refinement

if not patch_mask_refinement():
    raise SystemExit("runtime patch failed")

from manga_translator.args import parser

print(json.dumps({
    "parser": parser.prog,
    "onnxruntime": "onnxruntime" in sys.modules,
    "booru_tagger": "manga_translator.inpainting.booru_tagger" in sys.modules,
    "sd_inpainter": "manga_translator.inpainting.inpainting_sd" in sys.modules,
}, sort_keys=True))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(BACKEND_DIR.parent),
            text=True,
            capture_output=True,
            check=True,
        )
        loaded_modules = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(
            loaded_modules,
            {
                "parser": "manga_translator",
                "onnxruntime": False,
                "booru_tagger": False,
                "sd_inpainter": False,
            },
        )

    def test_vertical_renderer_columns_are_top_aligned(self) -> None:
        render_files = [
            BACKEND_DIR / "patched_text_render.py",
        ]
        vendor_text_render = BACKEND_DIR / "manga-image-translator" / "manga_translator" / "rendering" / "text_render.py"
        if vendor_text_render.exists():
            render_files.append(vendor_text_render)

        for render_file in render_files:
            with self.subTest(render_file=render_file.name):
                content = render_file.read_text(encoding="utf-8")
                self.assertNotIn("pen_line[1] += (max(line_height_list) - line_height) // 2", content)
                self.assertNotIn("pen_line[1] += max(line_height_list) - line_height", content)

    def test_horizontal_renderer_uses_left_aligned_inner_text_box(self) -> None:
        rendering = self.load_patched_rendering()

        class DummyRegion:
            _direction = "auto"
            alignment = "center"
            direction = "h"
            horizontal = True

        region = DummyRegion()
        self.assertEqual(rendering._render_alignment_for_direction(region, "h"), "left")
        self.assertEqual(rendering._render_alignment_for_direction(region, "horizontal"), "left")
        self.assertEqual(rendering._render_alignment_for_direction(region, "hr"), "right")
        self.assertEqual(
            rendering._select_region_layout(region, 48, 8, None, 120, 12, True, None, ""),
            ("h", 48),
        )

        tall_candidate = np.zeros((200, 50, 4), dtype=np.uint8)
        fits, overflow, fill = rendering._layout_metrics_for_direction(tall_candidate, 60, 12, "h")
        self.assertTrue(fits)
        self.assertLessEqual(overflow, 1.0)
        self.assertGreater(fill, 0)

        padding = rendering._text_box_padding(32, 100, 60)
        self.assertGreater(padding, 0)
        inner_width, inner_height, inner_padding = rendering._inner_text_box_size(100, 60, 32)
        self.assertEqual(inner_padding, padding)
        self.assertEqual(inner_width, 100 - padding * 2)
        self.assertEqual(inner_height, 60 - padding * 2)

        temp_box = np.zeros((20, 40, 4), dtype=np.uint8)
        temp_box[:, :, 3] = 255
        canvas = rendering._compose_render_canvas(temp_box, 100, 60, "left", True, padding)
        ys, xs = np.where(canvas[:, :, 3] > 0)

        self.assertEqual(int(xs.min()), padding)
        self.assertGreaterEqual(int(ys.min()), padding)
        self.assertLessEqual(int(xs.max()), 100 - padding - 1)
        self.assertLessEqual(int(ys.max()), 60 - padding - 1)

    def test_page_payload_exposes_font_size_override_for_preview_parity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            page_document = {
                "page_id": "page-1.png",
                "dimensions": {"width": 100, "height": 200},
                "regions": [{
                    "region_id": "region-1",
                    "bbox": [1, 2, 30, 40],
                    "direction": "h",
                    "source_text": "original",
                    "translation": {"machine": "translated"},
                    "style": {
                        "auto_font_style": "mincho",
                        "font_style_override": "cartoon",
                        "font_style": "cartoon",
                        "font_family": "CustomDialogue.otf",
                        "font_size": 24,
                        "font_size_override": 24,
                        "rotation": -12,
                        "stroke_width": 0,
                        "letter_spacing": 1.25,
                        "line_spacing": 1.35,
                        "fg_color": "#123456",
                        "bg_color": [250, 251, 252],
                    },
                    "flags": {
                        "preserve_background": True,
                    },
                }],
            }

            translation_page = engine._page_document_to_translation_page(page_document, "page-1.png")
            style_page = engine._page_document_to_style_page(page_document, "page-1.png")

            self.assertEqual(translation_page["regions"][0]["font_size_override"], 24)
            self.assertEqual(style_page["regions"][0]["font_size_override"], 24)
            self.assertEqual(translation_page["regions"][0]["rotation"], -12)
            self.assertEqual(style_page["regions"][0]["rotation"], -12)
            self.assertEqual(translation_page["regions"][0]["stroke_width"], 0)
            self.assertEqual(style_page["regions"][0]["stroke_width"], 0)
            self.assertEqual(translation_page["regions"][0]["letter_spacing"], 1.25)
            self.assertEqual(style_page["regions"][0]["line_spacing"], 1.35)
            self.assertEqual(translation_page["regions"][0]["fg_color"], [18, 52, 86])
            self.assertEqual(style_page["regions"][0]["bg_color"], [250, 251, 252])
            self.assertTrue(translation_page["regions"][0]["preserve_background"])
            self.assertTrue(style_page["regions"][0]["preserve_background"])
            self.assertEqual(translation_page["regions"][0]["auto_style"], "mincho")
            self.assertEqual(translation_page["regions"][0]["override_style"], "cartoon")
            self.assertEqual(translation_page["regions"][0]["resolved_style"], "cartoon")
            self.assertEqual(translation_page["regions"][0]["font_family"], "CustomDialogue.otf")

    def test_default_render_font_size_is_exposed_to_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            self.assertEqual(engine._resolve_render_font_size(30, None), 24)
            self.assertEqual(engine._resolve_render_font_size(10, None), 8)
            self.assertEqual(engine._resolve_render_font_size(30, 30), 30)
            self.assertEqual(engine._resolve_detected_font_size_from_render_size(24), 30)

    def test_v1_page_document_migrates_detected_size_to_effective_render_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            migrated = engine._migrate_page_document({
                "page_id": "page-1.png",
                "regions": [
                    {"region_id": "auto", "style": {"font_size": 30, "font_size_override": None}},
                    {"region_id": "edited", "style": {"font_size": 30, "font_size_override": 30}},
                ],
                "metadata": {"document_version": 1, "revision": 4},
            })

            self.assertEqual(migrated["regions"][0]["style"]["detected_font_size"], 30)
            self.assertEqual(migrated["regions"][0]["style"]["font_size"], 24)
            self.assertEqual(migrated["regions"][1]["style"]["font_size"], 30)
            self.assertEqual(migrated["metadata"]["document_version"], engine.PAGE_DOCUMENT_VERSION)
            self.assertEqual(migrated["metadata"]["revision"], 5)

    def test_translation_layout_overrides_normalize_advanced_style(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            normalized = engine._normalize_translation_region_layout_overrides({
                "region-1": {
                    "rotation": 240,
                    "stroke_width": -1,
                    "letter_spacing": 4,
                    "line_spacing": 0.1,
                    "fg_color": "#abc",
                    "bg_color": "#123456",
                    "preserve_background": True,
                }
            })

            self.assertEqual(normalized["region-1"]["rotation"], 180)
            self.assertEqual(normalized["region-1"]["stroke_width"], 0)
            self.assertEqual(normalized["region-1"]["letter_spacing"], 2.5)
            self.assertEqual(normalized["region-1"]["line_spacing"], 0.5)
            self.assertEqual(normalized["region-1"]["fg_color"], [170, 187, 204])
            self.assertEqual(normalized["region-1"]["bg_color"], [18, 52, 86])
            self.assertTrue(normalized["region-1"]["preserve_background"])

    def test_auto_text_background_color_falls_back_from_black_on_black(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            class Region:
                pass

            region = Region()
            region.fg_colors = np.array([8, 8, 8], dtype=np.uint8)
            region.bg_colors = np.array([0, 0, 0], dtype=np.uint8)

            engine._sanitize_auto_text_background_color(region, {})
            self.assertEqual(engine._rgb_color_payload(region.bg_colors, (0, 0, 0)), [255, 255, 255])

            region.bg_colors = np.array([0, 0, 0], dtype=np.uint8)
            engine._sanitize_auto_text_background_color(region, {"bg_color": [0, 0, 0]})
            self.assertEqual(engine._rgb_color_payload(region.bg_colors, (255, 255, 255)), [0, 0, 0])

    def test_numpy_region_colors_do_not_break_style_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            class Region:
                pass

            region = Region()
            region.xyxy = [8, 8, 42, 34]
            region.min_rect = [np.array([[8, 8], [42, 8], [42, 34], [8, 34]], dtype=np.float32)]
            region.fg_colors = np.array([8, 8, 8], dtype=np.uint8)
            region.bg_colors = np.array([255, 255, 255], dtype=np.uint8)
            region.font_size = 18
            region.text = "测试"
            region.translation = "测试"

            source_rgb = np.full((48, 56, 3), 255, dtype=np.uint8)
            source_rgb[14:28, 16:32] = 0
            features = engine._extract_region_style_features(source_rgb, region, 18)
            self.assertGreaterEqual(features["fill_ratio"], 0.0)

            payload = engine._build_manual_region_payload(
                stored_name="page-1.png",
                bbox=[8, 8, 42, 34],
                source_text="测试",
                translation="test",
                target_lang="CHS",
                fg_color=region.fg_colors,
                bg_color=region.bg_colors,
            )
            self.assertEqual(payload["fg_color"], [8, 8, 8])
            self.assertEqual(payload["bg_color"], [255, 255, 255])

    def test_manual_region_is_created_before_ocr_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            source_dir.mkdir()
            Image.new("RGB", (240, 320), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "manual_regions": {},
            }

            async def fail_ocr(*_args, **_kwargs):
                raise RuntimeError("OCR runtime unavailable")

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._ocr_manual_region = fail_ocr  # type: ignore[method-assign]

            region = asyncio.run(engine.create_manual_region(
                session_id="manual-project",
                session=session,
                raw_config={"translator": "none", "target_lang": "CHS", "use_gpu": False},
                stored_name="page-1.png",
                bbox=[20, 30, 140, 190],
            ))

            self.assertEqual(region["bbox"], [20, 30, 140, 190])
            self.assertEqual(region["source_text"], "")
            self.assertEqual(
                session["manual_regions"]["page-1.png"][0]["id"],
                region["id"],
            )

    def test_manual_region_survives_ocr_failure_and_can_be_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            source_dir.mkdir()
            Image.new("RGB", (240, 320), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "manual_regions": {},
            }
            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            region = asyncio.run(engine.create_manual_region(
                session_id="manual-project",
                session=session,
                raw_config={"translator": "none", "target_lang": "CHS", "use_gpu": False},
                stored_name="page-1.png",
                bbox=[20, 30, 140, 190],
            ))

            async def fail_ocr(*_args, **_kwargs):
                raise RuntimeError("OCR runtime unavailable")

            engine._ocr_manual_region = fail_ocr  # type: ignore[method-assign]
            retried = asyncio.run(engine.recognize_manual_region(
                session_id="manual-project",
                session=session,
                raw_config={"translator": "none", "target_lang": "CHS", "use_gpu": False},
                stored_name="page-1.png",
                region_id=region["id"],
            ))

            self.assertEqual(retried["id"], region["id"])
            self.assertEqual(retried["recognition_status"], "failed")
            self.assertIn("OCR runtime unavailable", retried["recognition_error"])
            self.assertEqual(
                session["manual_regions"]["page-1.png"][0]["id"],
                region["id"],
            )

    def test_manual_region_ocr_outlier_font_size_is_bounded_and_retry_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            source_dir.mkdir()
            Image.new("RGB", (240, 320), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "workflow_stage": "detected",
                "manual_regions": {},
            }
            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            region = asyncio.run(engine.create_manual_region(
                session_id="manual-project",
                session=session,
                raw_config={"translator": "none", "target_lang": "CHS", "use_gpu": False},
                stored_name="page-1.png",
                bbox=[20, 30, 140, 190],
            ))
            recommended_font_size = int(region["font_size"])

            async def outlier_ocr(*_args, **_kwargs):
                return {
                    "source_text": "测试",
                    "direction": "v",
                    "font_size": 9999,
                    "fg_color": (0, 0, 0),
                    "bg_color": (255, 255, 255),
                }

            engine._ocr_manual_region = outlier_ocr  # type: ignore[method-assign]
            first = asyncio.run(engine.recognize_manual_region(
                session_id="manual-project",
                session=session,
                raw_config={"translator": "none", "target_lang": "CHS", "use_gpu": False},
                stored_name="page-1.png",
                region_id=region["id"],
            ))
            second = asyncio.run(engine.recognize_manual_region(
                session_id="manual-project",
                session=session,
                raw_config={"translator": "none", "target_lang": "CHS", "use_gpu": False},
                stored_name="page-1.png",
                region_id=region["id"],
            ))

            self.assertLessEqual(first["font_size"], recommended_font_size * 2)
            self.assertEqual(second["font_size"], first["font_size"])

    def test_rerender_result_image_preserves_source_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_path = root / "source.png"
            Image.new("RGBA", (2, 2), (255, 255, 255, 0)).save(source_path)

            rendered_rgb = np.full((2, 2, 3), [12, 34, 56], dtype=np.uint8)
            result_image = engine._rendered_rgb_to_pil_image(source_path, rendered_rgb)

            self.assertEqual(result_image.mode, "RGBA")
            self.assertEqual(np.asarray(result_image.getchannel("A")).reshape(-1).tolist(), [0, 0, 0, 0])
            output_path = root / "nested" / "result.png"
            engine._save_result_atomic(result_image, output_path)
            self.assertTrue(output_path.exists())

    def test_page_image_response_path_generates_size_limited_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_path = root / "large-source.png"
            Image.new("RGB", (1200, 800), (12, 34, 56)).save(source_path)

            preview_path = engine.get_page_image_response_path(
                source_path,
                "project-a",
                "page-1.png",
                "source",
                320,
            )

            self.assertNotEqual(source_path, preview_path)
            self.assertTrue(preview_path.exists())
            with Image.open(preview_path) as preview_image:
                self.assertLessEqual(max(preview_image.size), 320)
                self.assertEqual(preview_image.size, (320, 213))

            cached_preview_path = engine.get_page_image_response_path(
                source_path,
                "project-a",
                "page-1.png",
                "source",
                320,
            )
            self.assertEqual(preview_path, cached_preview_path)

    def test_page_image_response_path_uses_cache_before_resizing_again(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_path = root / "large-source.png"
            Image.new("RGB", (1200, 800), (12, 34, 56)).save(source_path)

            preview_path = engine.get_page_image_response_path(
                source_path,
                "project-a",
                "page-1.png",
                "source",
                320,
            )

            with mock.patch.object(Image.Image, "resize", side_effect=AssertionError("cache miss")):
                cached_preview_path = engine.get_page_image_response_path(
                    source_path,
                    "project-a",
                    "page-1.png",
                    "source",
                    320,
                )

            self.assertEqual(preview_path, cached_preview_path)

    def test_page_image_response_path_keeps_original_when_preview_is_unneeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_path = root / "small-source.png"
            Image.new("RGB", (160, 120), (255, 255, 255)).save(source_path)

            self.assertEqual(
                source_path,
                engine.get_page_image_response_path(source_path, "project-a", "page-1.png", "source"),
            )
            self.assertEqual(
                source_path,
                engine.get_page_image_response_path(source_path, "project-a", "page-1.png", "source", 0),
            )
            self.assertEqual(
                source_path,
                engine.get_page_image_response_path(source_path, "project-a", "page-1.png", "source", 480),
            )

    def test_inspection_can_load_one_persisted_page_without_rebuilding_all_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-inspection"
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [
                    {"name": "page 1", "stored_name": "page-1.png"},
                    {"name": "page 2", "stored_name": "page-2.png"},
                ],
                "translated_output_map": {},
                "last_config": {},
                "workflow_stage": "translated",
            }

            for page_id in ("page-1.png", "page-2.png"):
                Image.new("RGB", (120, 160), (255, 255, 255)).save(source_dir / page_id)
                engine._write_json_file(
                    engine._project_page_document_path(project_id, page_id),
                    {
                        "version": engine.PAGE_DOCUMENT_VERSION,
                        "page_id": page_id,
                        "dimensions": {"width": 120, "height": 160},
                        "regions": [],
                    },
                )

            with mock.patch.object(
                engine,
                "_build_page_document",
                side_effect=AssertionError("persisted page should be reused"),
            ):
                review_payload = asyncio.run(
                    engine.inspect_translation_regions(
                        project_id,
                        session,
                        {},
                        target_stored_name="page-2.png",
                    )
                )
                style_payload = asyncio.run(
                    engine.inspect_style_regions(
                        project_id,
                        session,
                        {},
                        target_stored_name="page-2.png",
                    )
                )

            self.assertEqual([page["stored_name"] for page in review_payload["pages"]], ["page-2.png"])
            self.assertEqual([page["stored_name"] for page in style_payload["pages"]], ["page-2.png"])
            self.assertEqual(review_payload["pages"][0]["translated_image_url"], "")
            self.assertEqual(style_payload["pages"][0]["translated_image_url"], "")

    def test_project_glossary_preview_uses_previous_translation_as_replacement_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")

            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "translated_output_map": {},
                "workflow_stage": "translated",
                "last_config": {},
                "translation_region_overrides": {},
                "translation_region_skip_overrides": {},
                "translation_region_disabled_overrides": {},
                "translation_region_layout_overrides": {},
                "style_region_overrides": {},
                "project_glossary": {
                    "entries": [{
                        "id": "term-yamada",
                        "source": "山田",
                        "translation": "Yamada",
                        "category": "人名",
                    }]
                },
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "dimensions": {"width": 16, "height": 16},
                "regions": [
                    {
                        "region_id": "r1",
                        "bbox": [0, 0, 8, 8],
                        "source_text": "山田来了",
                        "translation": {"machine": "Yamada来了", "resolved": "Yamada来了"},
                    },
                    {
                        "region_id": "r2",
                        "bbox": [8, 0, 8, 8],
                        "source_text": "山田也在",
                        "translation": {"machine": "山田先生也在", "resolved": "山田先生也在"},
                    },
                    {
                        "region_id": "r3",
                        "bbox": [0, 8, 8, 8],
                        "source_text": "普通对白",
                        "translation": {"machine": "Yamada", "resolved": "Yamada"},
                    },
                ],
            })

            preview = engine.preview_project_glossary_application(project_id, session, [{
                "id": "term-yamada",
                "source": "山田",
                "translation": "山田先生",
                "category": "人名",
            }])

            self.assertEqual(preview["change_count"], 1)
            self.assertEqual(preview["changes"][0]["region_id"], "r1")
            self.assertEqual(preview["changes"][0]["before"], "Yamada来了")
            self.assertEqual(preview["changes"][0]["after"], "山田先生来了")

    def test_project_glossary_apply_sets_overrides_and_rerenders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")

            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "translated_output_map": {},
                "download_path": "",
                "workflow_stage": "translated",
                "last_config": {"translator": "custom_openai", "target_lang": "CHS"},
                "translation_region_overrides": {},
                "translation_region_skip_overrides": {},
                "translation_region_disabled_overrides": {},
                "translation_region_layout_overrides": {},
                "style_region_overrides": {},
                "project_glossary": {
                    "entries": [{
                        "id": "term-yamada",
                        "source": "山田",
                        "translation": "Yamada",
                        "category": "人名",
                    }]
                },
                "artifact_state": ProjectArtifactState.create(
                    ["page-1.png"]
                ).apply(
                    "page-1.png",
                    PageArtifactEvent.RECOGNIZED,
                ).apply(
                    "page-1.png",
                    PageArtifactEvent.TRANSLATED,
                ).model_dump(mode="json"),
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "dimensions": {"width": 16, "height": 16},
                "regions": [{
                    "region_id": "r1",
                    "bbox": [0, 0, 8, 8],
                    "source_text": "山田来了",
                    "translation": {"machine": "Yamada来了", "resolved": "Yamada来了"},
                }],
            })
            rerender_calls: list[dict[str, object]] = []

            async def fake_rerender_core(**kwargs):
                rerender_calls.append(kwargs)
                Image.new("RGB", (16, 16), (240, 240, 240)).save(output_dir / "page-1.png")
                session["translated_output_map"] = {"page-1.png": "page-1.png"}
                session["download_path"] = str(root / "translated.zip")
                return {
                    "download_url": f"/api/download/{project_id}",
                    "download_path": session["download_path"],
                    "translated_dir": str(output_dir),
                    "workflow_stage": "translated",
                }

            engine._rerender_session_core = fake_rerender_core  # type: ignore[method-assign]

            result = asyncio.run(engine.apply_project_glossary(project_id, session, [{
                "id": "term-yamada",
                "source": "山田",
                "translation": "山田先生",
                "category": "人名",
            }]))

            self.assertEqual(session["translation_region_overrides"]["r1"], "山田先生来了")
            self.assertEqual(result["change_count"], 1)
            self.assertEqual(result["glossary"]["entries"][0]["translation"], "山田先生")
            self.assertEqual(len(rerender_calls), 1)
            page_artifact = result["page_artifacts"]["page-1.png"]
            self.assertEqual(page_artifact["artifacts"]["translation"]["revision"], 2)
            self.assertTrue(page_artifact["capabilities"]["final_stale"])

    def test_project_glossary_save_preserves_previous_translation_for_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-2.png")

            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [
                    {"name": "page-1.png", "stored_name": "page-1.png"},
                    {"name": "page-2.png", "stored_name": "page-2.png"},
                ],
                "translated_output_map": {},
                "download_path": "",
                "workflow_stage": "translated",
                "last_config": {"translator": "custom_openai", "target_lang": "CHS"},
                "translation_region_overrides": {},
                "translation_region_skip_overrides": {},
                "translation_region_disabled_overrides": {},
                "translation_region_layout_overrides": {},
                "style_region_overrides": {},
                "project_glossary": {
                    "entries": [{
                        "id": "term-yamada",
                        "source": "山田",
                        "translation": "Yamada",
                        "category": "人名",
                    }]
                },
                "artifact_state": ProjectArtifactState.create(
                    ["page-1.png", "page-2.png"]
                ).apply(
                    "page-1.png",
                    PageArtifactEvent.RECOGNIZED,
                ).apply(
                    "page-1.png",
                    PageArtifactEvent.TRANSLATED,
                ).apply(
                    "page-2.png",
                    PageArtifactEvent.RECOGNIZED,
                ).apply(
                    "page-2.png",
                    PageArtifactEvent.TRANSLATED,
                ).model_dump(mode="json"),
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "dimensions": {"width": 16, "height": 16},
                "regions": [{
                    "region_id": "r1",
                    "bbox": [0, 0, 8, 8],
                    "source_text": "山田来了",
                    "translation": {"machine": "Yamada来了", "resolved": "Yamada来了"},
                }],
            })
            engine._write_json_file(engine._project_page_document_path(project_id, "page-2.png"), {
                "page_id": "page-2.png",
                "dimensions": {"width": 16, "height": 16},
                "regions": [{
                    "region_id": "r2",
                    "bbox": [0, 0, 8, 8],
                    "source_text": "普通对白",
                    "translation": {"machine": "普通对白", "resolved": "普通对白"},
                }],
            })
            saved_glossary = engine.save_project_glossary(project_id, session, [{
                "id": "term-yamada",
                "source": "山田",
                "translation": "山田先生",
                "category": "人名",
            }])
            self.assertEqual(saved_glossary["entries"][0]["replacement"], "Yamada")

            rerender_targets: list[str | None] = []

            async def fake_rerender_core(**kwargs):
                target = kwargs.get("target_stored_name")
                rerender_targets.append(target)
                stored_name = str(target or "page-1.png")
                Image.new("RGB", (16, 16), (240, 240, 240)).save(output_dir / stored_name)
                session["translated_output_map"][stored_name] = stored_name
                return {
                    "download_url": f"/api/download/{project_id}",
                    "download_path": session.get("download_path", ""),
                    "translated_dir": str(output_dir),
                    "workflow_stage": "translated",
                }

            engine._rerender_session_core = fake_rerender_core  # type: ignore[method-assign]
            engine.build_session_archive = lambda *_args, **_kwargs: str(root / "translated.zip")  # type: ignore[method-assign]

            result = asyncio.run(engine.apply_project_glossary(project_id, session, saved_glossary["entries"]))

            self.assertEqual(session["translation_region_overrides"]["r1"], "山田先生来了")
            self.assertEqual(result["change_count"], 1)
            self.assertEqual(rerender_targets, ["page-1.png"])

    def test_project_glossary_apply_can_replace_untranslated_source_term(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "project_glossary": {
                    "entries": [{
                        "id": "term-ren",
                        "source": "蓮",
                        "translation": "莲",
                        "category": "人名",
                    }]
                },
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "dimensions": {"width": 16, "height": 16},
                "regions": [{
                    "region_id": "r1",
                    "bbox": [0, 0, 8, 8],
                    "source_text": "蓮来了",
                    "translation": {"machine": "蓮来了", "resolved": "蓮来了"},
                }],
            })

            preview = engine.preview_project_glossary_application(project_id, session)

            self.assertEqual(preview["change_count"], 1)
            self.assertEqual(preview["changes"][0]["after"], "莲来了")

    def test_project_glossary_lightweight_read_skips_occurrence_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            session = {
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "project_glossary": {
                    "entries": [{
                        "id": "term-yamada",
                        "source": "山田",
                        "translation": "山田",
                        "category": "人名",
                    }]
                },
            }
            engine.get_page_document = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not scan pages"))  # type: ignore[method-assign]

            glossary = engine.get_project_glossary("project-glossary", session)

            self.assertFalse(glossary["occurrences_loaded"])
            self.assertIsNone(glossary["entries"][0]["occurrence_count"])

    def test_project_glossary_extraction_uses_direct_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "project_glossary": {"entries": []},
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "dimensions": {"width": 16, "height": 16},
                "regions": [{
                    "region_id": "r1",
                    "bbox": [0, 0, 8, 8],
                    "source_text": "山田去了星见町",
                    "translation": {},
                }],
            })

            async def fail_translation_dispatcher(*_args, **_kwargs):
                raise AssertionError("glossary extraction should not use the translation dispatcher")

            async def fake_completion(config, prompt):
                self.assertEqual(config["selected_translator"], "openai-compatible")
                self.assertIn("山田去了星见町", prompt)
                return '[{"source":"山田","translation":"山田","category":"人名"}]'

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._translate_text_batch = fail_translation_dispatcher  # type: ignore[method-assign]
            engine._request_project_glossary_extraction = fake_completion  # type: ignore[method-assign]
            glossary = asyncio.run(engine.extract_project_glossary(project_id, session, {
                "translator": "custom_openai",
                "selected_translator": "openai-compatible",
                "target_lang": "CHS",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "example-model",
                "api_key": "secret",
            }))

            self.assertEqual(glossary["entries"][0]["source"], "山田")
            self.assertEqual(glossary["entries"][0]["category"], "人名")

    def test_project_glossary_extraction_accepts_structured_chat_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "project_glossary": {"entries": []},
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "regions": [{
                    "region_id": "r1",
                    "bbox": [0, 0, 8, 8],
                    "source_text": "私の名前は片桐 奈々美",
                    "translation": {},
                }],
            })

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._post_validation_json = lambda **_kwargs: {  # type: ignore[method-assign]
                "choices": [{
                    "message": {
                        "content": [{
                            "type": "text",
                            "text": '[{"source":"片桐 奈々美","translation":"片桐奈奈美","category":"人名"}]',
                        }],
                    },
                }],
            }
            glossary = asyncio.run(engine.extract_project_glossary(project_id, session, {
                "translator": "openai-compatible",
                "selected_translator": "openai-compatible",
                "target_lang": "CHS",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "example-model",
                "api_key": "secret",
            }, force=True))

            self.assertEqual(glossary["entries"][0]["source"], "片桐 奈々美")
            self.assertEqual(glossary["entries"][0]["translation"], "片桐奈奈美")

    def test_project_glossary_parser_accepts_chinese_keys_and_wrappers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            entries = engine._parse_glossary_extraction_response(json.dumps({
                "terminology": {
                    "小夏": {
                        "译文": "小夏",
                        "类别": "角色名",
                        "说明": "角色名",
                    },
                    "星见町": {
                        "translation": "星见町",
                        "category": "地名",
                    },
                }
            }, ensure_ascii=False))

            self.assertEqual([entry["source"] for entry in entries], ["小夏", "星见町"])
            self.assertEqual(entries[0]["category"], "人名")
            self.assertEqual(entries[1]["category"], "地点")

            single_entry = engine._parse_glossary_extraction_response(
                '{"原文":"蓮","译文":"莲","类别":"角色名"}'
            )
            self.assertEqual(single_entry[0]["source"], "蓮")
            self.assertEqual(single_entry[0]["translation"], "莲")

    def test_project_glossary_parser_accepts_labeled_non_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            entries = engine._parse_glossary_extraction_response(
                "提取结果：\n"
                "- 原文：片桐 奈々美；译文：片桐奈奈美；类别：人名；说明：女主角\n"
                "- source: 星見町; translation: 星见町; category: 地点"
            )

            self.assertEqual([entry["source"] for entry in entries], ["片桐 奈々美", "星見町"])
            self.assertEqual(entries[0]["translation"], "片桐奈奈美")
            self.assertEqual(entries[0]["category"], "人名")
            self.assertEqual(entries[1]["category"], "地点")

    def test_project_glossary_extraction_retries_when_model_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "project_glossary": {"entries": []},
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "dimensions": {"width": 16, "height": 16},
                "regions": [{
                    "region_id": "r1",
                    "bbox": [0, 0, 8, 8],
                    "source_text": "小夏和蓮去了星见町",
                    "translation": {},
                }],
            })
            prompts: list[str] = []

            async def fake_completion(config, prompt):
                prompts.append(prompt)
                if len(prompts) == 1:
                    return "[]"
                self.assertIn("2 到 4 个字", prompt)
                return '```json\n{"items":[{"原文":"小夏","译文":"小夏","类别":"角色名"}]}\n```'

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._request_project_glossary_extraction = fake_completion  # type: ignore[method-assign]
            glossary = asyncio.run(engine.extract_project_glossary(project_id, session, {
                "translator": "openai-compatible",
                "selected_translator": "openai-compatible",
                "target_lang": "CHS",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "example-model",
                "api_key": "secret",
            }))

            self.assertEqual(len(prompts), 2)
            self.assertEqual(glossary["entries"][0]["source"], "小夏")
            self.assertEqual(glossary["entries"][0]["category"], "人名")

    def test_project_glossary_retry_keeps_full_context_and_lets_the_model_choose_name_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "project_glossary": {
                    "entries": [{
                        "id": "term-rubber-suit",
                        "source": "ラバースーツ",
                        "translation": "橡胶衣",
                        "category": "道具",
                    }],
                },
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "dimensions": {"width": 16, "height": 16},
                "regions": [
                    {
                        "region_id": "r1",
                        "bbox": [0, 0, 8, 8],
                        "source_text": "ハァハァななみちゃーん！",
                        "translation": {},
                    },
                    {
                        "region_id": "r2",
                        "bbox": [0, 8, 16, 16],
                        "source_text": "私の名前は片桐 奈々美",
                        "translation": {},
                    },
                    {
                        "region_id": "r3",
                        "bbox": [8, 0, 16, 8],
                        "source_text": "ど…どうしたの？奈々美ちゃん",
                        "translation": {},
                    },
                    {
                        "region_id": "r4",
                        "bbox": [8, 8, 16, 16],
                        "source_text": "無関但需要保留的全局上下文",
                        "translation": {},
                    },
                ],
            })
            prompts: list[str] = []

            async def fake_completion(_config, prompt):
                prompts.append(prompt)
                if len(prompts) == 2 and "可能遗漏的 OCR 证据" in prompt:
                    return '[{"source":"片桐 奈々美","translation":"片桐奈奈美","category":"人名"}]'
                return "[]"

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._request_project_glossary_extraction = fake_completion  # type: ignore[method-assign]
            glossary = asyncio.run(engine.extract_project_glossary(project_id, session, {
                "translator": "openai-compatible",
                "selected_translator": "openai-compatible",
                "target_lang": "CHS",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "example-model",
                "api_key": "secret",
            }, force=True))

            self.assertEqual(len(prompts), 2)
            self.assertIn("可能遗漏的 OCR 证据", prompts[-1])
            self.assertIn("不是预先切好的词条", prompts[-1])
            self.assertIn("無関但需要保留的全局上下文", prompts[-1])
            self.assertEqual(
                {entry["source"] for entry in glossary["entries"]},
                {"ラバースーツ", "片桐 奈々美"},
            )
            self.assertTrue(glossary["auto_extract_completed"])

    def test_project_glossary_empty_result_remains_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "project_glossary": {"entries": []},
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "regions": [{
                    "region_id": "r1",
                    "bbox": [0, 0, 8, 8],
                    "source_text": "山田和小夏在这里",
                    "translation": {"machine": "", "resolved": ""},
                }],
            })
            calls = 0

            async def fake_completion(_config, _prompt):
                nonlocal calls
                calls += 1
                return "[]"

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._request_project_glossary_extraction = fake_completion  # type: ignore[method-assign]
            config = {
                "translator": "openai-compatible",
                "selected_translator": "openai-compatible",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "model",
                "api_key": "secret",
                "target_lang": "CHS",
            }

            glossary = asyncio.run(engine.extract_project_glossary(project_id, session, config))
            glossary_again = asyncio.run(engine.extract_project_glossary(project_id, session, config))
            glossary_forced = asyncio.run(engine.extract_project_glossary(project_id, session, config, force=True))

            self.assertFalse(glossary["auto_extract_completed"])
            self.assertFalse(glossary_again["auto_extract_completed"])
            self.assertFalse(glossary_forced["auto_extract_completed"])
            self.assertEqual(calls, 6)

    def test_project_glossary_retries_a_legacy_completed_empty_library(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "project_glossary": {
                    "entries": [],
                    "auto_extract_completed": True,
                    "auto_extracted_at": "2026-07-07T12:56:55+00:00",
                },
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "regions": [{
                    "region_id": "r1",
                    "source_text": "私の名前は片桐 奈々美",
                    "translation": {},
                }],
            })

            async def fake_completion(_config, _prompt):
                return '[{"source":"片桐 奈々美","translation":"片桐奈奈美","category":"人名"}]'

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._request_project_glossary_extraction = fake_completion  # type: ignore[method-assign]
            glossary = asyncio.run(engine.extract_project_glossary(project_id, session, {
                "translator": "openai-compatible",
                "selected_translator": "openai-compatible",
                "target_lang": "CHS",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "example-model",
                "api_key": "secret",
            }))

            self.assertEqual(glossary["entries"][0]["source"], "片桐 奈々美")
            self.assertTrue(glossary["auto_extract_completed"])

    def test_project_glossary_extraction_uses_fallback_for_translation_only_doubao_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)

            captured: dict[str, str] = {}

            def fake_chat_text(**kwargs):
                captured["model"] = kwargs["model"]
                captured["user_prompt"] = kwargs["user_prompt"]
                return '[{"source":"小夏","translation":"小夏","category":"人名"}]'

            engine._request_chat_completions_text_sync = fake_chat_text  # type: ignore[method-assign]
            result = asyncio.run(engine._request_project_glossary_extraction({
                "translator": "custom_openai",
                "selected_translator": "doubao-ark",
                "translator_model": "doubao-seed-translation-250915",
                "api_key": "secret",
            }, "项目 OCR 原文"))

            self.assertIn("小夏", result)
            self.assertEqual(captured["model"], engine.DOUBAO_GLOSSARY_FALLBACK_MODEL)
            self.assertIn("项目 OCR 原文", captured["user_prompt"])

    def test_project_glossary_openai_adapter_accepts_reasoning_content_when_final_content_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            engine._post_validation_json = lambda **_kwargs: {  # type: ignore[method-assign]
                "choices": [{
                    "message": {
                        "content": "",
                        "reasoning_content": (
                            '[{"source":"片桐 奈々美","translation":"片桐奈奈美","category":"人名"}]'
                        ),
                    },
                }],
            }

            response = asyncio.run(engine._request_project_glossary_extraction({
                "translator": "openai-compatible",
                "selected_translator": "openai-compatible",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "reasoning-model",
                "api_key": "secret",
            }, "项目 OCR 原文"))

            self.assertIn("片桐 奈々美", response)

    def test_project_glossary_extraction_reports_missing_ocr_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "project_glossary": {"entries": []},
            }

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            glossary = asyncio.run(engine.extract_project_glossary(project_id, session, {
                "translator": "openai-compatible",
                "selected_translator": "openai-compatible",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "model",
                "api_key": "secret",
                "target_lang": "CHS",
            }, force=True))

            self.assertEqual(glossary["entries"], [])
            self.assertIn("先识别文本框", glossary["extract_message"])

    def test_text_mask_completion_catches_symbol_stroke_fragments(self) -> None:
        mask_utils = self.load_patched_text_mask_utils()

        class DummyAabb:
            xywh = (10, 10, 28, 28)

        class DummyTextLine:
            aabb = DummyAabb()
            font_size = 20
            area = 28 * 28

        image = np.full((48, 48, 3), 255, dtype=np.uint8)
        heart_points = np.array(
            [[13, 21], [16, 15], [22, 18], [28, 15], [34, 21], [24, 34], [13, 21]],
            dtype=np.int32,
        )
        cv2.polylines(image, [heart_points], False, (0, 0, 0), 2, lineType=cv2.LINE_8)
        cv2.circle(image, (44, 4), 2, (0, 0, 0), -1)

        partial_mask = np.zeros(image.shape[:2], dtype=np.uint8)
        cv2.polylines(partial_mask, [heart_points], False, 255, 2, lineType=cv2.LINE_8)
        partial_mask[:, :18] = 0

        enhanced, added = mask_utils._complete_ink_component_residuals(
            image,
            partial_mask,
            [DummyTextLine()],
        )

        self.assertGreater(int(added[21, 14]), 0)
        self.assertGreater(int(enhanced[21, 14]), 0)
        self.assertEqual(int(enhanced[4, 44]), 0)

    def test_resume_translation_skips_completed_pages_and_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-resume"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (8, 8), (255, 255, 255)).save(source_dir / "page-1.png")
            Image.new("RGB", (8, 8), (240, 240, 240)).save(source_dir / "page-2.png")
            Image.new("RGB", (8, 8), (12, 34, 56)).save(output_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [
                    {"name": "page-1.png", "stored_name": "page-1.png"},
                    {"name": "page-2.png", "stored_name": "page-2.png"},
                ],
                "translated_output_map": {"page-1.png": "page-1.png"},
                "download_path": "",
                "workflow_stage": "detected",
                "last_config": {"rerender_output_format": "png"},
                "project_glossary": {"entries": [], "auto_extract_completed": True},
                "translation_region_overrides": {},
                "translation_region_skip_overrides": {},
                "translation_region_disabled_overrides": {},
                "translation_region_layout_overrides": {},
                "style_region_overrides": {},
                "artifact_state": ProjectArtifactState.create(
                    ["page-1.png", "page-2.png"]
                ).apply(
                    "page-1.png",
                    PageArtifactEvent.RECOGNIZED,
                ).apply(
                    "page-2.png",
                    PageArtifactEvent.RECOGNIZED,
                ).model_dump(mode="json"),
            }
            page_doc_path = engine._project_page_document_path(project_id, "page-1.png")
            page_doc_path.parent.mkdir(parents=True, exist_ok=True)
            page_doc_path.write_text(json.dumps({
                "page_id": "page-1.png",
                "regions": [
                    {
                        "region_id": "region-1",
                        "source_text": "山田",
                        "translation": {"machine": "Yamada", "resolved": "Yamada"},
                        "flags": {},
                    }
                ],
            }), encoding="utf-8")
            rendered_pages: list[str] = []
            persisted_pages: list[list[str] | None] = []
            events: list[dict[str, object]] = []

            async def fake_translate_regions(*_args, **_kwargs) -> None:
                return None

            async def fake_render_cached_page(*_args, **kwargs) -> None:
                output_path = kwargs.get("output_path") if "output_path" in kwargs else _args[1]
                rendered_pages.append(Path(output_path).name)
                Image.new("RGB", (8, 8), (90, 90, 90)).save(output_path)

            async def collect_event(event: dict[str, object]) -> None:
                events.append(event)

            def fake_persist_project_state(_project_id, _session, **kwargs) -> None:
                persisted_pages.append(kwargs.get("page_ids"))

            def fake_archive(*_args, **_kwargs) -> str:
                archive_path = root / "translated.zip"
                archive_path.write_bytes(b"zip")
                return str(archive_path)

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._ensure_editable_page_cache = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
            engine._prepare_cached_regions_for_edit = lambda *_args, **_kwargs: []  # type: ignore[method-assign]
            engine._translate_cached_regions = fake_translate_regions  # type: ignore[method-assign]
            engine._persist_translated_regions = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            engine._render_cached_page = fake_render_cached_page  # type: ignore[method-assign]
            engine.persist_project_state = fake_persist_project_state  # type: ignore[method-assign]
            engine.build_session_archive = fake_archive  # type: ignore[method-assign]

            result = asyncio.run(engine._resume_translation_session(
                session_id=project_id,
                session=session,
                raw_config={"rerender_output_format": "png"},
                progress_callback=collect_event,
                skip_completed=True,
                page_checkpoints={"page-1.png": "rendered"},
            ))

            self.assertEqual(rendered_pages, ["page-2.png"])
            self.assertEqual(session["translated_output_map"]["page-1.png"], "page-1.png")
            self.assertEqual(session["translated_output_map"]["page-2.png"], "page-2.png")
            self.assertIn(["page-2.png"], persisted_pages)
            self.assertEqual(session["workflow_stage"], "translated")
            self.assertTrue(result["download_path"].endswith("translated.zip"))
            start_events = [event for event in events if event.get("event") == "start"]
            self.assertEqual(start_events[0]["total_pages"], 1)
            page_artifact = engine.build_client_session_payload(
                project_id,
                session,
            )["page_artifacts"]["page-2.png"]
            self.assertTrue(page_artifact["capabilities"]["translation_ready"])
            self.assertTrue(page_artifact["capabilities"]["final_ready"])
            self.assertTrue(page_artifact["capabilities"]["can_export"])

    def test_resume_translation_does_not_skip_detect_only_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-resume-detected"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (8, 8), (255, 255, 255)).save(source_dir / "page-1.png")
            Image.new("RGB", (8, 8), (240, 240, 240)).save(output_dir / "page-1.png")
            page_doc_path = engine._project_page_document_path(project_id, "page-1.png")
            page_doc_path.parent.mkdir(parents=True, exist_ok=True)
            page_doc_path.write_text(json.dumps({
                "page_id": "page-1.png",
                "regions": [
                    {
                        "region_id": "region-1",
                        "source_text": "山田",
                        "translation": {"machine": "", "resolved": ""},
                        "flags": {},
                    }
                ],
            }), encoding="utf-8")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [
                    {"name": "page-1.png", "stored_name": "page-1.png"},
                ],
                "translated_output_map": {"page-1.png": "page-1.png"},
                "download_path": "",
                "workflow_stage": "detected",
                "last_config": {"rerender_output_format": "png"},
                "project_glossary": {"entries": []},
                "translation_region_overrides": {},
                "translation_region_skip_overrides": {},
                "translation_region_disabled_overrides": {},
                "translation_region_layout_overrides": {},
                "style_region_overrides": {},
                "artifact_state": ProjectArtifactState.create(
                    ["page-1.png"]
                ).apply(
                    "page-1.png",
                    PageArtifactEvent.RECOGNIZED,
                ).model_dump(mode="json"),
            }
            rendered_pages: list[str] = []
            events: list[dict[str, object]] = []

            async def fake_translate_regions(*_args, **_kwargs) -> None:
                return None

            async def fake_render_cached_page(*_args, **kwargs) -> None:
                output_path = kwargs.get("output_path") if "output_path" in kwargs else _args[1]
                rendered_pages.append(Path(output_path).name)
                Image.new("RGB", (8, 8), (90, 90, 90)).save(output_path)

            async def collect_event(event: dict[str, object]) -> None:
                events.append(event)

            async def fake_glossary_request(*_args, **_kwargs) -> str:
                return '[{"source":"山田","translation":"山田","category":"人名"}]'

            def fake_archive(*_args, **_kwargs) -> str:
                archive_path = root / "translated.zip"
                archive_path.write_bytes(b"zip")
                return str(archive_path)

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._ensure_editable_page_cache = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
            engine._prepare_cached_regions_for_edit = lambda *_args, **_kwargs: []  # type: ignore[method-assign]
            engine._translate_cached_regions = fake_translate_regions  # type: ignore[method-assign]
            engine._persist_translated_regions = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            engine._render_cached_page = fake_render_cached_page  # type: ignore[method-assign]
            engine.persist_project_state = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            engine.build_session_archive = fake_archive  # type: ignore[method-assign]
            engine._request_project_glossary_extraction = fake_glossary_request  # type: ignore[method-assign]

            asyncio.run(engine._resume_translation_session(
                session_id=project_id,
                session=session,
                raw_config={"rerender_output_format": "png"},
                progress_callback=collect_event,
                skip_completed=True,
            ))

            self.assertEqual(rendered_pages, ["page-1.png"])
            self.assertEqual(session["project_glossary"]["entries"][0]["source"], "山田")
            self.assertTrue(session["project_glossary"]["auto_extract_completed"])
            start_events = [event for event in events if event.get("event") == "start"]
            self.assertEqual(start_events[0]["total_pages"], 1)

    def test_render_page_working_set_skips_archive_rebuild_and_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (4, 4), (255, 255, 255)).save(source_dir / "page-1.png")
            Image.new("RGB", (4, 4), (240, 240, 240)).save(source_dir / "page-2.png")
            existing_archive = root / "existing.zip"
            existing_archive.write_bytes(b"existing")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "rerender_cache_dir": str(root / "cache"),
                "source_images": [
                    {"name": "page-1.png", "stored_name": "page-1.png"},
                    {"name": "page-2.png", "stored_name": "page-2.png"},
                ],
                "translated_output_map": {"page-2.png": "page-2.png"},
                "download_path": str(existing_archive),
                "workflow_stage": "translated",
                "last_config": {"rerender_output_format": "png"},
                "artifact_state": ProjectArtifactState.create(
                    ["page-1.png", "page-2.png"]
                ).apply(
                    "page-1.png",
                    PageArtifactEvent.RECOGNIZED,
                ).apply(
                    "page-1.png",
                    PageArtifactEvent.TRANSLATED,
                ).apply(
                    "page-2.png",
                    PageArtifactEvent.RECOGNIZED,
                ).apply(
                    "page-2.png",
                    PageArtifactEvent.TRANSLATED,
                ).model_dump(mode="json"),
            }
            events: list[dict[str, object]] = []

            async def fake_render_cached_page(*_args, **kwargs) -> None:
                output_path = kwargs.get("output_path") if "output_path" in kwargs else _args[1]
                Image.new("RGB", (4, 4), (12, 34, 56)).save(output_path)

            async def collect_event(event: dict[str, object]) -> None:
                events.append(event)

            def fail_archive(*_args, **_kwargs) -> str:
                raise AssertionError("single-page rerender should not rebuild the archive synchronously")

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._ensure_editable_page_cache = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
            engine._render_cached_page = fake_render_cached_page  # type: ignore[method-assign]
            engine.build_session_archive = fail_archive  # type: ignore[method-assign]
            engine.persist_project_state(
                "project-a",
                session,
                persist_page_documents=True,
            )
            base = engine.project_workspace.read_command_base(
                "project-a",
                "page-1.png",
            )

            with (
                engine.project_workspace.materialize_page_working_set(base) as working_set,
                mock.patch.object(
                    engine,
                    "persist_project_state",
                    side_effect=AssertionError(
                        "RenderPage preparation must not persist or commit"
                    ),
                ) as persist_project_state,
            ):
                prepared = asyncio.run(
                    engine.render_page_working_set(
                        working_set=working_set,
                        raw_config={"rerender_output_format": "png"},
                        progress_callback=collect_event,
                    )
                )
                rendered_logical_path = next(
                    logical_path
                    for logical_path in prepared.artifact_files
                    if logical_path.startswith("translated/")
                )
                rendered_file = prepared.artifact_files[rendered_logical_path]
                self.assertTrue(rendered_file.is_file())

            persist_project_state.assert_not_called()
            self.assertEqual(
                prepared.execution_extras["download_url"],
                "/api/download/project-a",
            )
            self.assertEqual(
                prepared.execution_extras["download_path"],
                str(existing_archive.resolve()),
            )
            self.assertEqual(prepared.runtime_session["workflow_stage"], "translated")
            self.assertIn(
                "page-1.png",
                prepared.runtime_session["translated_output_map"],
            )
            self.assertEqual(
                prepared.page_documents["page-1.png"]["metadata"]["revision"],
                base.page_revision + 1,
            )
            self.assertEqual(events[-1]["event"], "progress")
            self.assertEqual(events[-1]["current"], 1)
            self.assertEqual(events[-1]["total"], 1)
            page_artifact = ProjectArtifactState.model_validate(
                prepared.runtime_session["artifact_state"]
            ).page_view("page-1.png")
            self.assertEqual(page_artifact.artifacts.final.revision, 2)
            self.assertTrue(page_artifact.capabilities.final_ready)

    def test_advanced_erase_composite_preserves_pixels_outside_change_mask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((80, 80, 3), 255, dtype=np.uint8)
            source[30:46, 20:62] = 0
            edited = np.full((80, 80, 3), 255, dtype=np.uint8)

            composite, mask, changed_ratio = engine._composite_advanced_erase_result(source, edited)

            self.assertGreater(int(mask[38, 36]), 0)
            self.assertGreater(changed_ratio, 0)
            self.assertLess(changed_ratio, 0.2)
            self.assertTrue(np.array_equal(composite[5, 5], source[5, 5]))
            self.assertGreater(int(composite[38, 36, 0]), 200)

    def test_advanced_erase_composite_never_feathers_outside_the_safe_mask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((100, 100, 3), [24, 48, 72], dtype=np.uint8)
            edited = np.full((100, 100, 3), [232, 216, 184], dtype=np.uint8)
            safe_mask = np.zeros((100, 100), dtype=np.uint8)
            safe_mask[30:70, 30:70] = 255

            composite, _mask, _changed_ratio = engine._composite_advanced_erase_result(
                source,
                edited,
                change_mask=safe_mask,
            )

            self.assertTrue(np.array_equal(composite[29, 50], source[29, 50]))
            self.assertTrue(np.array_equal(composite[50, 29], source[50, 29]))
            self.assertTrue(np.array_equal(composite[50, 50], edited[50, 50]))

    def test_advanced_erase_rejects_full_page_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((80, 80, 3), 255, dtype=np.uint8)
            edited = np.full((80, 80, 3), 120, dtype=np.uint8)

            with self.assertRaises(RuntimeError):
                engine._composite_advanced_erase_result(source, edited)

    def test_seedream_request_size_rounds_up_to_min_pixels(self) -> None:
        client = SeedreamImageCleanupClient(api_key="secret", model="seedream-test")
        source = np.full((1400, 900, 3), 255, dtype=np.uint8)

        prepared_source, prepared_guide, size_value = client._prepare_request_images(source, None)

        width, height = [int(part) for part in size_value.split("x")]
        self.assertIsNone(prepared_guide)
        self.assertGreaterEqual(width * height, client.MIN_PIXELS)
        self.assertEqual(prepared_source.shape[:2], (height, width))

    def test_advanced_erase_prompt_is_direct_and_covers_embedded_text(self) -> None:
        prompt = translator_module.ADVANCED_IMAGE_ERASE_PROMPT
        normalized = " ".join(prompt.lower().split())

        self.assertLess(len(prompt), 900)
        self.assertIn("speech", normalized)
        self.assertIn("borderless", normalized)
        self.assertIn("sound effects", normalized)
        self.assertIn("diagonal", normalized)
        self.assertIn("reconstruct", normalized)
        self.assertIn("do not create", normalized)
        self.assertIn("new speech bubble", normalized)
        self.assertIn("preserve", normalized)
        self.assertIn("clearly visible", normalized)
        self.assertIn("never infer", normalized)
        self.assertIn("flat white", normalized)

    def test_seedream_auth_error_explains_configuration_in_natural_language(self) -> None:
        client = SeedreamImageCleanupClient(api_key="invalid", model="wrong-model")
        source = np.full((1440, 2560, 3), 255, dtype=np.uint8)
        error_body = BytesIO(json.dumps({
            "error": {
                "code": "AuthenticationError",
                "message": "The API key or AK/SK in the request is missing or invalid.",
                "type": "Unauthorized",
            }
        }).encode("utf-8"))
        http_error = urllib_error.HTTPError(
            client.api_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=error_body,
        )

        with mock.patch("engine.image_cleanup.urllib_request.urlopen", side_effect=http_error):
            with self.assertRaises(RuntimeError) as captured:
                client._remove_text_sync(source, None, "remove text")

        message = str(captured.exception)
        self.assertIn("认证失败", message)
        self.assertIn("API Key", message)
        self.assertIn("模型名称", message)
        self.assertNotIn('{"error"', message)

    def test_advanced_erase_region_mask_limits_full_page_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((80, 80, 3), 255, dtype=np.uint8)
            edited = np.full((80, 80, 3), 120, dtype=np.uint8)
            allowed_mask = np.zeros((80, 80), dtype=np.uint8)
            allowed_mask[30:50, 20:60] = 255
            final_mask = engine._advanced_erase_final_mask(
                engine._build_advanced_erase_change_mask(source, edited),
                allowed_mask,
            )

            composite, mask, changed_ratio = engine._composite_advanced_erase_result(
                source,
                edited,
                change_mask=final_mask,
            )

            self.assertGreater(int(mask[40, 30]), 0)
            self.assertEqual(int(mask[10, 10]), 0)
            self.assertLess(changed_ratio, engine.ADVANCED_ERASE_MAX_CHANGED_RATIO)
            self.assertTrue(np.array_equal(composite[10, 10], source[10, 10]))
            self.assertEqual(int(composite[40, 30, 0]), 120)

    def test_advanced_erase_final_mask_does_not_replace_entire_allowed_region(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            raw_diff_mask = np.zeros((120, 120), dtype=np.uint8)
            raw_diff_mask[52:68, 56:64] = 255
            allowed_mask = np.zeros((120, 120), dtype=np.uint8)
            allowed_mask[16:104, 20:100] = 255

            final_mask = engine._advanced_erase_final_mask(raw_diff_mask, allowed_mask)

            self.assertGreater(int(final_mask[60, 60]), 0)
            self.assertEqual(int(final_mask[24, 28]), 0)
            self.assertLessEqual(
                int(cv2.countNonZero(final_mask)),
                int(cv2.countNonZero(raw_diff_mask)) * 2,
            )

    def test_advanced_erase_uses_detected_text_regions_as_allowed_mask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            region_mask = np.zeros((120, 120), dtype=np.uint8)
            region_mask[44:76, 48:72] = 255

            selected, mode = engine._select_advanced_erase_allowed_mask(region_mask)

            self.assertIsNotNone(selected)
            assert selected is not None
            self.assertEqual(mode, "detected_text_regions")
            self.assertGreater(int(selected[60, 60]), 0)
            self.assertEqual(int(selected[20, 20]), 0)

    def test_advanced_erase_safe_mask_preserves_color_art_and_monochrome_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            color_source = np.full((240, 240, 3), [236, 190, 142], dtype=np.uint8)
            color_source[42:102, 36:96] = [82, 146, 218]
            color_source[112:126, 114:126] = 12
            color_edited = color_source.copy()
            color_edited[42:102, 36:96] = 255
            color_edited[112:126, 114:126] = [236, 190, 142]
            color_allowed = np.zeros((240, 240), dtype=np.uint8)
            color_allowed[24:152, 20:154] = 255

            color_mask = engine._build_advanced_erase_safe_change_mask(
                color_source,
                color_edited,
                color_allowed,
            )

            self.assertGreater(int(color_mask[118, 120]), 0)
            self.assertEqual(int(color_mask[72, 62]), 0)

            mono_source = np.full((240, 240, 3), 255, dtype=np.uint8)
            cv2.rectangle(mono_source, (34, 32), (206, 208), (0, 0, 0), 3)
            mono_source[112:128, 112:128] = 0
            mono_edited = mono_source.copy()
            cv2.rectangle(mono_edited, (34, 32), (206, 208), (255, 255, 255), 3)
            mono_edited[112:128, 112:128] = 255
            mono_allowed = np.zeros((240, 240), dtype=np.uint8)
            mono_allowed[20:220, 20:220] = 255

            mono_mask = engine._build_advanced_erase_safe_change_mask(
                mono_source,
                mono_edited,
                mono_allowed,
            )

            self.assertGreater(int(mono_mask[120, 120]), 0)
            self.assertEqual(int(mono_mask[32, 80]), 0)

    def test_advanced_erase_safe_mask_removes_diagonal_text_without_accepting_new_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            height = width = 220
            yy, xx = np.indices((height, width))
            background = np.stack(
                [
                    154 + (xx % 17),
                    176 + (yy % 19),
                    198 + ((xx + yy) % 13),
                ],
                axis=2,
            ).astype(np.uint8)
            source = background.copy()
            cv2.line(source, (24, 34), (196, 34), (28, 28, 28), 3)

            text_mask = np.zeros((height, width), dtype=np.uint8)
            for offset in (0, 18, 36):
                cv2.line(text_mask, (76 + offset, 72), (56 + offset, 118), 255, 5)
                cv2.line(text_mask, (62 + offset, 94), (82 + offset, 94), 255, 4)
            source[text_mask > 0] = [24, 24, 30]

            edited = source.copy()
            edited[text_mask > 0] = background[text_mask > 0]
            edited[32:37, 82:138] = background[32:37, 82:138]
            cv2.rectangle(edited, (42, 58), (152, 138), (18, 18, 18), 3)

            allowed = np.zeros((height, width), dtype=np.uint8)
            allowed[24:150, 34:166] = 255
            safe_mask = engine._build_advanced_erase_safe_change_mask(source, edited, allowed)

            self.assertGreater(int(safe_mask[90, 68]), 0)
            self.assertEqual(int(safe_mask[34, 100]), 0)
            self.assertEqual(int(safe_mask[58, 48]), 0)

    def test_advanced_erase_safe_mask_rejects_a_shifted_panel_border(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((240, 240, 3), 255, dtype=np.uint8)
            source[48:62, 104:136] = [24, 24, 28]
            cv2.line(source, (18, 80), (222, 80), (24, 24, 28), 3)

            edited = source.copy()
            edited[48:62, 104:136] = 255
            cv2.line(edited, (18, 80), (222, 80), (255, 255, 255), 3)
            cv2.line(edited, (18, 72), (222, 72), (24, 24, 28), 3)

            allowed = np.zeros((240, 240), dtype=np.uint8)
            allowed[34:94, 10:230] = 255
            safe_mask = engine._build_advanced_erase_safe_change_mask(
                source,
                edited,
                allowed,
            )

            self.assertGreater(int(safe_mask[54, 120]), 0)
            self.assertEqual(int(safe_mask[72, 120]), 0)
            self.assertEqual(int(safe_mask[80, 120]), 0)

    def test_advanced_erase_safe_mask_rejects_a_new_enclosing_outline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((240, 240, 3), [184, 196, 208], dtype=np.uint8)
            source[96:144, 114:126] = [22, 24, 28]

            edited = source.copy()
            edited[96:144, 114:126] = [184, 196, 208]
            cv2.ellipse(edited, (120, 120), (14, 28), 0, 0, 360, (22, 24, 28), 3)

            allowed = np.zeros((240, 240), dtype=np.uint8)
            allowed[80:160, 92:148] = 255
            safe_mask = engine._build_advanced_erase_safe_change_mask(
                source,
                edited,
                allowed,
            )

            self.assertGreater(int(safe_mask[120, 120]), 0)
            self.assertEqual(int(safe_mask[92, 120]), 0)
            self.assertEqual(int(safe_mask[120, 106]), 0)

    def test_advanced_erase_safe_mask_rejects_new_white_container_over_embedded_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            height = width = 240
            yy, xx = np.indices((height, width))
            background = np.stack(
                [
                    138 + (xx % 23),
                    166 + (yy % 19),
                    192 + ((xx + yy) % 17),
                ],
                axis=2,
            ).astype(np.uint8)
            source = background.copy()
            for x in (88, 104, 120, 136, 152):
                cv2.line(source, (x, 82), (x, 158), (24, 24, 30), 6)
                cv2.line(source, (x - 6, 98), (x + 6, 98), (24, 24, 30), 5)
                cv2.line(source, (x - 6, 126), (x + 6, 126), (24, 24, 30), 5)

            edited = source.copy()
            edited[68:174, 72:168] = [234, 234, 234]
            allowed = np.zeros((height, width), dtype=np.uint8)
            allowed[60:182, 64:176] = 255

            safe_mask = engine._build_advanced_erase_safe_change_mask(
                source,
                edited,
                allowed,
            )

            self.assertEqual(int(safe_mask[120, 120]), 0)
            self.assertEqual(int(cv2.countNonZero(safe_mask[74:168, 78:162])), 0)

    def test_advanced_erase_safe_mask_allows_text_removal_inside_existing_white_bubble(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((240, 240, 3), [158, 184, 208], dtype=np.uint8)
            cv2.ellipse(source, (120, 120), (42, 62), 0, 0, 360, (252, 252, 252), -1)
            cv2.ellipse(source, (120, 120), (42, 62), 0, 0, 360, (24, 24, 30), 3)
            for y in (92, 112, 132, 152):
                cv2.line(source, (112, y), (128, y), (24, 24, 30), 5)

            edited = source.copy()
            for y in (92, 112, 132, 152):
                cv2.line(edited, (112, y), (128, y), (252, 252, 252), 5)
            allowed = np.zeros((240, 240), dtype=np.uint8)
            allowed[74:166, 92:148] = 255

            safe_mask = engine._build_advanced_erase_safe_change_mask(
                source,
                edited,
                allowed,
            )

            self.assertGreater(int(safe_mask[112, 120]), 0)
            self.assertGreater(int(safe_mask[152, 120]), 0)

    def test_advanced_erase_bright_container_guard_preserves_existing_bubble_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((240, 240, 3), [156, 182, 208], dtype=np.uint8)
            cv2.ellipse(source, (120, 120), (36, 54), 0, 0, 360, (252, 252, 252), -1)
            source[108:132, 116:124] = [24, 24, 30]

            edited = source.copy()
            edited[62:178, 78:162] = [250, 250, 250]
            allowed = np.zeros((240, 240), dtype=np.uint8)
            allowed[58:182, 74:166] = 255

            protected = engine._build_advanced_erase_novel_bright_container_protection_mask(
                source,
                edited,
                allowed,
            )

            self.assertGreater(int(protected[70, 86]), 0)
            self.assertEqual(int(protected[120, 120]), 0)

    def test_advanced_erase_bright_container_guard_does_not_treat_white_page_as_artwork_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((240, 240, 3), 255, dtype=np.uint8)
            source[42:198, 42:198] = [112, 138, 166]
            source[88:154, 112:128] = [24, 24, 30]

            edited = source.copy()
            edited[70:176, 76:164] = [244, 244, 244]
            allowed = np.zeros((240, 240), dtype=np.uint8)
            allowed[62:184, 68:172] = 255

            protected = engine._build_advanced_erase_novel_bright_container_protection_mask(
                source,
                edited,
                allowed,
            )

            self.assertGreater(int(protected[120, 120]), 0)

    def test_advanced_erase_page_makes_one_cleanup_request_without_white_postfill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()

            source = np.full((100, 100, 3), [232, 218, 196], dtype=np.uint8)
            source[42:58, 46:54] = [20, 20, 24]
            Image.fromarray(source).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }
            cache_dir = engine._session_page_cache_dir(session, "project-a", "page-1.png")
            cache_dir.mkdir(parents=True)
            Image.fromarray(source).save(cache_dir / "inpainted.png")

            allowed = np.zeros((100, 100), dtype=np.uint8)
            allowed[30:70, 34:66] = 255
            observed_prompts: list[str] = []

            class FakeClient:
                async def remove_text(self, source_rgb, _guide_rgb=None, prompt="", **_kwargs):
                    observed_prompts.append(prompt)
                    edited = source_rgb.copy()
                    edited[42:58, 46:54] = [232, 218, 196]
                    return edited

            engine._build_advanced_erase_allowed_mask = lambda *_args, **_kwargs: (allowed, 1)
            engine._apply_page_artifact_event = lambda *_args, **_kwargs: None
            engine.persist_project_state = lambda *_args, **_kwargs: None
            engine.build_client_session_payload = lambda *_args, **_kwargs: {}

            with mock.patch.object(translator_module, "create_image_cleanup_client", return_value=FakeClient()):
                result = asyncio.run(engine.advanced_erase_page(
                    project_id="project-a",
                    session=session,
                    page_id="page-1.png",
                    raw_config={
                        "advanced_erase_provider": "volcengine-ark",
                        "advanced_erase_base_url": "https://ark.example.com/api/v3/images/generations",
                        "advanced_erase_model": "custom-seedream-model",
                        "advanced_erase_api_key": "secret",
                    },
                ))

            self.assertEqual(result["advanced_erase"]["action"], "erase")
            self.assertEqual(observed_prompts, [translator_module.ADVANCED_IMAGE_ERASE_PROMPT])
            self.assertFalse(hasattr(engine, "_clean_advanced_erase_white_container_residue"))
            output = np.array(Image.open(cache_dir / "inpainted.png").convert("RGB"))
            self.assertTrue(np.array_equal(output[50, 50], [232, 218, 196]))

    def test_repeated_advanced_erase_continues_from_previous_cleaned_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()

            background = np.full((120, 120, 3), [214, 184, 146], dtype=np.uint8)
            source = background.copy()
            source[34:48, 32:42] = [18, 18, 22]
            source[70:86, 76:86] = [18, 18, 22]
            Image.fromarray(source).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }
            cache_dir = engine._session_page_cache_dir(session, "project-a", "page-1.png")
            cache_dir.mkdir(parents=True)
            Image.fromarray(source).save(cache_dir / "inpainted.png")

            allowed = np.zeros((120, 120), dtype=np.uint8)
            allowed[24:56, 20:54] = 255
            allowed[62:96, 66:98] = 255
            observed_inputs: list[np.ndarray] = []

            class FakeClient:
                async def remove_text(self, source_rgb, _guide_rgb=None, prompt="", **_kwargs):
                    observed_inputs.append(source_rgb.copy())
                    edited = source_rgb.copy()
                    if len(observed_inputs) == 1:
                        edited[34:48, 32:42] = background[34:48, 32:42]
                    else:
                        edited[70:86, 76:86] = background[70:86, 76:86]
                    return edited

            engine._build_advanced_erase_allowed_mask = lambda *_args, **_kwargs: (allowed, 2)
            engine._apply_page_artifact_event = lambda *_args, **_kwargs: None
            engine.persist_project_state = lambda *_args, **_kwargs: None
            engine.build_client_session_payload = lambda *_args, **_kwargs: {}
            raw_config = {
                "advanced_erase_provider": "volcengine-ark",
                "advanced_erase_base_url": "https://ark.example.com/api/v3/images/generations",
                "advanced_erase_model": "custom-seedream-model",
                "advanced_erase_api_key": "secret",
            }

            with mock.patch.object(translator_module, "create_image_cleanup_client", return_value=FakeClient()):
                asyncio.run(engine.advanced_erase_page(
                    "project-a",
                    session,
                    "page-1.png",
                    raw_config,
                ))
                asyncio.run(engine.advanced_erase_page(
                    "project-a",
                    session,
                    "page-1.png",
                    raw_config,
                ))

            self.assertEqual(len(observed_inputs), 2)
            self.assertTrue(np.array_equal(observed_inputs[1][40, 36], background[40, 36]))
            self.assertTrue(np.array_equal(observed_inputs[1][78, 80], [18, 18, 22]))
            output = np.array(Image.open(cache_dir / "inpainted.png").convert("RGB"))
            self.assertTrue(np.array_equal(output[40, 36], background[40, 36]))
            self.assertTrue(np.array_equal(output[78, 80], background[78, 80]))

    def test_advanced_erase_region_mask_uses_clean_result_without_source_bleed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((80, 80, 3), 255, dtype=np.uint8)
            source[36:44, 30:50] = 0
            edited = np.full((80, 80, 3), 255, dtype=np.uint8)
            allowed_mask = np.zeros((80, 80), dtype=np.uint8)
            allowed_mask[30:50, 20:60] = 255
            final_mask = engine._advanced_erase_final_mask(
                engine._build_advanced_erase_change_mask(source, edited),
                allowed_mask,
            )

            composite, mask, changed_ratio = engine._composite_advanced_erase_result(
                source,
                edited,
                change_mask=final_mask,
            )

            self.assertGreater(changed_ratio, 0)
            self.assertEqual(int(mask[40, 40]), 255)
            self.assertTrue(np.array_equal(composite[40, 40], edited[40, 40]))
            self.assertTrue(np.array_equal(composite[10, 10], source[10, 10]))

    def test_selection_erase_input_blanks_outside_rects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            base = np.zeros((80, 80, 3), dtype=np.uint8)
            base[:, :] = [32, 96, 160]
            base[20:40, 10:30] = [0, 0, 0]

            rects = engine._normalize_selection_erase_rects(
                [{"x": 0.125, "y": 0.25, "width": 0.25, "height": 0.25}],
                base.shape,
            )
            mask = engine._build_selection_erase_mask(rects, base.shape)
            selected_input = engine._build_selection_erase_input_image(base, mask)

            self.assertEqual(rects, [(10, 20, 30, 40)])
            self.assertTrue(np.array_equal(selected_input[5, 5], [255, 255, 255]))
            self.assertTrue(np.array_equal(selected_input[25, 15], base[25, 15]))

    def test_selection_erase_composite_keeps_pixels_outside_rects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            base = np.zeros((80, 80, 3), dtype=np.uint8)
            base[:, :] = [210, 210, 210]
            base[24:28, 14:18] = [0, 0, 0]
            edited = np.full((80, 80, 3), 240, dtype=np.uint8)
            mask = np.zeros((80, 80), dtype=np.uint8)
            mask[20:40, 10:30] = 255

            composite, changed_ratio, precise_mask, model_change_mask, text_mask, residual_mask = (
                engine._composite_selection_erase_result(base, edited, mask)
            )

            self.assertGreater(changed_ratio, 0)
            self.assertGreater(int(cv2.countNonZero(precise_mask)), 0)
            self.assertGreater(int(cv2.countNonZero(model_change_mask)), 0)
            self.assertGreater(int(cv2.countNonZero(text_mask)), 0)
            self.assertEqual(int(cv2.countNonZero(residual_mask)), 0)
            self.assertTrue(np.array_equal(composite[5, 5], base[5, 5]))
            self.assertTrue(np.array_equal(composite[25, 15], edited[25, 15]))
            self.assertFalse(np.array_equal(composite[30, 25], base[30, 25]))

    def test_selection_erase_composite_inpaints_unchanged_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            base = np.full((80, 80, 3), 245, dtype=np.uint8)
            cv2.line(base, (24, 26), (30, 34), (0, 0, 0), 2)
            cv2.line(base, (36, 26), (30, 34), (0, 0, 0), 2)
            edited = base.copy()
            mask = np.zeros((80, 80), dtype=np.uint8)
            mask[18:44, 18:44] = 255

            composite, changed_ratio, precise_mask, model_change_mask, text_mask, residual_mask = (
                engine._composite_selection_erase_result(base, edited, mask)
            )

            self.assertGreater(changed_ratio, 0)
            self.assertEqual(int(cv2.countNonZero(model_change_mask)), 0)
            self.assertGreater(int(cv2.countNonZero(text_mask)), 0)
            self.assertGreater(int(cv2.countNonZero(residual_mask)), 0)
            self.assertGreater(int(composite[30, 30, 0]), 150)
            self.assertTrue(np.array_equal(composite[5, 5], base[5, 5]))

    def test_selection_erase_page_sends_only_selected_area_to_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            source = np.full((80, 80, 3), [64, 64, 64], dtype=np.uint8)
            Image.fromarray(source).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }
            cache_dir = engine._session_page_cache_dir(session, "project-a", "page-1.png")
            cache_dir.mkdir(parents=True)
            base = np.full((80, 80, 3), [220, 220, 220], dtype=np.uint8)
            base[24:28, 14:18] = [0, 0, 0]
            Image.fromarray(base).save(cache_dir / "inpainted.png")
            observed_inputs: list[np.ndarray] = []
            observed_prompts: list[str] = []

            class FakeClient:
                async def remove_text(self, source_rgb, _guide_rgb=None, prompt="", **_kwargs):
                    observed_inputs.append(source_rgb.copy())
                    observed_prompts.append(prompt)
                    edited = source_rgb.copy()
                    edited[20:40, 10:30] = [245, 245, 245]
                    return edited

            original_factory = translator_module.create_image_cleanup_client
            translator_module.create_image_cleanup_client = lambda **_kwargs: FakeClient()
            try:
                result = asyncio.run(engine.advanced_erase_page(
                    project_id="project-a",
                    session=session,
                    page_id="page-1.png",
                    raw_config={
                        "advanced_erase_provider": "volcengine-ark",
                        "advanced_erase_base_url": "https://ark.example.com/api/v3/images/generations",
                        "advanced_erase_model": "custom-seedream-model",
                        "advanced_erase_api_key": "secret",
                        "advanced_erase_selection_prompt": "custom selection prompt",
                    },
                    action="selection",
                    selections=[{"x": 0.125, "y": 0.25, "width": 0.25, "height": 0.25}],
                ))
            finally:
                translator_module.create_image_cleanup_client = original_factory

            self.assertEqual(result["advanced_erase"]["action"], "selection")
            self.assertEqual(len(observed_inputs), 1)
            self.assertEqual(observed_prompts, ["custom selection prompt"])
            self.assertTrue(np.array_equal(observed_inputs[0][5, 5], [255, 255, 255]))
            self.assertTrue(np.array_equal(observed_inputs[0][25, 15], base[25, 15]))
            output = np.array(Image.open(cache_dir / "inpainted.png").convert("RGB"))
            self.assertTrue(np.array_equal(output[5, 5], base[5, 5]))
            self.assertTrue(np.array_equal(output[25, 15], [245, 245, 245]))
            self.assertFalse(np.array_equal(output[38, 28], base[38, 28]))

    def test_local_model_selection_erase_does_not_require_remote_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            source = np.full((80, 80, 3), [64, 64, 64], dtype=np.uint8)
            Image.fromarray(source).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }
            cache_dir = engine._session_page_cache_dir(session, "project-a", "page-1.png")
            cache_dir.mkdir(parents=True)
            base = np.full((80, 80, 3), [220, 220, 220], dtype=np.uint8)
            base[24:28, 14:18] = [0, 0, 0]
            Image.fromarray(base).save(cache_dir / "inpainted.png")
            observed_masks: list[np.ndarray] = []

            async def fake_lama(base_rgb, selection_mask, *, device):
                observed_masks.append(selection_mask.copy())
                edited = base_rgb.copy()
                edited[selection_mask > 0] = [245, 245, 245]
                return edited

            engine._select_local_inpainting_device = lambda _use_gpu: "cpu"
            engine._run_local_lama_inpaint = fake_lama

            result = asyncio.run(engine.advanced_erase_page(
                project_id="project-a",
                session=session,
                page_id="page-1.png",
                raw_config={},
                action="local-selection",
                selections=[{"x": 0.125, "y": 0.25, "width": 0.25, "height": 0.25}],
            ))

            self.assertEqual(result["advanced_erase"]["action"], "local-selection")
            self.assertEqual(result["advanced_erase"]["model"], "lama_large")
            self.assertEqual(result["advanced_erase"]["device"], "cpu")
            self.assertEqual(len(observed_masks), 1)
            self.assertEqual(int(observed_masks[0][25, 15]), 255)
            self.assertEqual(int(observed_masks[0][5, 5]), 0)
            output = np.array(Image.open(cache_dir / "inpainted.png").convert("RGB"))
            self.assertTrue(np.array_equal(output[5, 5], base[5, 5]))
            self.assertFalse(np.array_equal(output[25, 15], base[25, 15]))

    def test_local_model_selection_erase_default_mask_preserves_bubble_outline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            source = np.full((120, 120, 3), 220, dtype=np.uint8)
            Image.fromarray(source).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }
            cache_dir = engine._session_page_cache_dir(session, "project-a", "page-1.png")
            cache_dir.mkdir(parents=True)
            base = np.full((120, 120, 3), 220, dtype=np.uint8)
            cv2.ellipse(base, (60, 60), (25, 40), 0, 0, 360, (255, 255, 255), -1)
            cv2.ellipse(base, (60, 60), (25, 40), 0, 0, 360, (0, 0, 0), 2)
            base[54:66, 56:64] = 0
            Image.fromarray(base).save(cache_dir / "inpainted.png")
            observed_masks: list[np.ndarray] = []

            async def fake_lama(base_rgb, selection_mask, *, device):
                observed_masks.append(selection_mask.copy())
                edited = base_rgb.copy()
                edited[selection_mask > 0] = [245, 245, 245]
                return edited

            engine._select_local_inpainting_device = lambda _use_gpu: "cpu"
            engine._run_local_lama_inpaint = fake_lama

            result = asyncio.run(engine.advanced_erase_page(
                project_id="project-a",
                session=session,
                page_id="page-1.png",
                raw_config={},
                action="local-selection",
                selections=[{"x": 0.25, "y": 0.12, "width": 0.5, "height": 0.76}],
            ))

            self.assertEqual(result["advanced_erase"]["mask_mode"], "text")
            self.assertEqual(len(observed_masks), 1)
            self.assertEqual(int(observed_masks[0][60, 60]), 255)
            self.assertEqual(int(observed_masks[0][20, 60]), 0)
            output = np.array(Image.open(cache_dir / "inpainted.png").convert("RGB"))
            self.assertTrue(np.array_equal(output[20, 60], base[20, 60]))
            self.assertFalse(np.array_equal(output[60, 60], base[60, 60]))

    def test_local_advanced_erase_preview_does_not_replace_current_blank_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            source = np.full((120, 120, 3), 238, dtype=np.uint8)
            source[50:66, 52:70] = 12
            Image.fromarray(source).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }
            cache_dir = engine._session_page_cache_dir(session, "project-a", "page-1.png")
            cache_dir.mkdir(parents=True)
            current_blank = np.full((120, 120, 3), 180, dtype=np.uint8)
            Image.fromarray(current_blank).save(cache_dir / "inpainted.png")
            (cache_dir / "regions.json").write_text(json.dumps([
                {
                    "lines": [[[46, 44], [76, 44], [76, 72], [46, 72]]],
                    "texts": ["文字"],
                    "font_size": 18,
                    "angle": 0,
                    "translation": "",
                }
            ]), encoding="utf-8")

            async def fake_lama(base_rgb, erase_mask, *, device):
                edited = base_rgb.copy()
                edited[erase_mask > 0] = [246, 246, 246]
                return edited

            async def fake_detect(_source_rgb, **_kwargs):
                return {"mask": np.zeros(source.shape[:2], dtype=np.uint8), "textlines": []}

            engine.inference_backend.detect_text_mask = fake_detect
            engine._select_local_inpainting_device = lambda _use_gpu: "cpu"
            engine._run_local_lama_inpaint = fake_lama

            result = asyncio.run(engine.advanced_erase_page(
                project_id="project-a",
                session=session,
                page_id="page-1.png",
                raw_config={},
                action="local-advanced-preview",
            ))

            self.assertEqual(result["advanced_erase"]["action"], "local-advanced-preview")
            self.assertEqual(result["advanced_erase"]["model"], "lama_large")
            self.assertFalse(result["advanced_erase"]["detector_fallback_used"])
            self.assertGreater(result["advanced_erase"]["erase_ratio"], 0)
            self.assertTrue(result["advanced_erase"]["preview"]["candidate_url"])
            unchanged_blank = np.array(Image.open(cache_dir / "inpainted.png").convert("RGB"))
            self.assertTrue(np.array_equal(unchanged_blank, current_blank))

            async def failed_detect(_source_rgb, **_kwargs):
                raise RuntimeError("detector unavailable")

            engine.inference_backend.detect_text_mask = failed_detect
            fallback = asyncio.run(engine.advanced_erase_page(
                project_id="project-a",
                session=session,
                page_id="page-1.png",
                raw_config={},
                action="local-advanced-preview",
            ))
            self.assertTrue(fallback["advanced_erase"]["detector_fallback_used"])

    def test_local_advanced_erase_retries_with_smaller_size_after_cuda_oom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            source = np.full((120, 120, 3), 238, dtype=np.uint8)
            source[50:66, 52:70] = 12
            Image.fromarray(source).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }
            cache_dir = engine._session_page_cache_dir(session, "project-a", "page-1.png")
            cache_dir.mkdir(parents=True)
            Image.fromarray(source).save(cache_dir / "inpainted.png")
            (cache_dir / "regions.json").write_text(json.dumps([
                {
                    "lines": [[[46, 44], [76, 44], [76, 72], [46, 72]]],
                    "texts": ["文字"],
                    "font_size": 18,
                    "angle": 0,
                    "translation": "",
                }
            ]), encoding="utf-8")

            async def fake_detect(_source_rgb, **_kwargs):
                return {"mask": np.zeros(source.shape[:2], dtype=np.uint8), "textlines": []}

            async def fake_initial_lama(_base_rgb, _erase_mask, *, device):
                self.assertEqual(device, "cuda")
                raise RuntimeError("CUDA out of memory")

            retry_sizes: list[int] = []

            async def fake_retry_lama(
                base_rgb,
                erase_mask,
                *,
                model_dir,
                device,
                inpainting_size,
            ):
                self.assertEqual(model_dir, engine.model_dir)
                self.assertEqual(device, "cuda")
                retry_sizes.append(inpainting_size)
                edited = base_rgb.copy()
                edited[erase_mask > 0] = [246, 246, 246]
                return edited

            engine.inference_backend.detect_text_mask = fake_detect
            engine.inference_backend.erase_selection = fake_retry_lama
            engine._select_local_inpainting_device = lambda _use_gpu: "cuda"
            engine._run_local_lama_inpaint = fake_initial_lama

            result = asyncio.run(engine.advanced_erase_page(
                project_id="project-a",
                session=session,
                page_id="page-1.png",
                raw_config={},
                action="local-advanced-preview",
            ))

            advanced_erase = result["advanced_erase"]
            self.assertEqual(retry_sizes, [engine.LOCAL_ADVANCED_ERASE_FALLBACK_SIZE])
            self.assertTrue(advanced_erase["fallback_used"])
            self.assertEqual(
                advanced_erase["inpainting_size"],
                engine.LOCAL_ADVANCED_ERASE_FALLBACK_SIZE,
            )

    def test_local_advanced_erase_apply_replaces_blank_page_with_preview_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            source = np.full((120, 120, 3), 238, dtype=np.uint8)
            source[50:66, 52:70] = 12
            Image.fromarray(source).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }
            cache_dir = engine._session_page_cache_dir(session, "project-a", "page-1.png")
            cache_dir.mkdir(parents=True)
            Image.fromarray(np.full((120, 120, 3), 180, dtype=np.uint8)).save(cache_dir / "inpainted.png")
            (cache_dir / "regions.json").write_text(json.dumps([
                {
                    "lines": [[[46, 44], [76, 44], [76, 72], [46, 72]]],
                    "texts": ["文字"],
                    "font_size": 18,
                    "angle": 0,
                    "translation": "",
                }
            ]), encoding="utf-8")

            async def fake_lama(base_rgb, erase_mask, *, device):
                edited = base_rgb.copy()
                edited[erase_mask > 0] = [246, 246, 246]
                return edited

            async def fake_detect(_source_rgb, **_kwargs):
                return {"mask": np.zeros(source.shape[:2], dtype=np.uint8), "textlines": []}

            engine.inference_backend.detect_text_mask = fake_detect
            engine._select_local_inpainting_device = lambda _use_gpu: "cpu"
            engine._run_local_lama_inpaint = fake_lama
            preview = asyncio.run(engine.advanced_erase_page(
                project_id="project-a",
                session=session,
                page_id="page-1.png",
                raw_config={},
                action="local-advanced-preview",
            ))

            result = asyncio.run(engine.advanced_erase_page(
                project_id="project-a",
                session=session,
                page_id="page-1.png",
                raw_config={},
                action="local-advanced-apply",
                attempt_id=preview["advanced_erase"]["attempt_id"],
            ))

            self.assertEqual(result["advanced_erase"]["action"], "local-advanced-apply")
            self.assertEqual(
                session["advanced_erase_pages"]["page-1.png"]["mode"],
                "local_advanced",
            )
            applied = np.array(Image.open(cache_dir / "inpainted.png").convert("RGB"))
            self.assertTrue(np.array_equal(applied[0, 0], source[0, 0]))
            self.assertTrue(np.array_equal(applied[56, 60], [246, 246, 246]))

    def test_local_advanced_erase_rejects_stale_preview_after_blank_page_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            source = np.full((120, 120, 3), 238, dtype=np.uint8)
            source[50:66, 52:70] = 12
            Image.fromarray(source).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }
            cache_dir = engine._session_page_cache_dir(session, "project-a", "page-1.png")
            cache_dir.mkdir(parents=True)
            Image.fromarray(source).save(cache_dir / "inpainted.png")
            (cache_dir / "regions.json").write_text(json.dumps([
                {
                    "lines": [[[46, 44], [76, 44], [76, 72], [46, 72]]],
                    "texts": ["文字"],
                    "font_size": 18,
                    "angle": 0,
                    "translation": "",
                }
            ]), encoding="utf-8")

            async def fake_lama(base_rgb, erase_mask, *, device):
                edited = base_rgb.copy()
                edited[erase_mask > 0] = [246, 246, 246]
                return edited

            async def fake_detect(_source_rgb, **_kwargs):
                return {"mask": np.zeros(source.shape[:2], dtype=np.uint8), "textlines": []}

            engine.inference_backend.detect_text_mask = fake_detect
            engine._select_local_inpainting_device = lambda _use_gpu: "cpu"
            engine._run_local_lama_inpaint = fake_lama
            preview = asyncio.run(engine.advanced_erase_page(
                project_id="project-a",
                session=session,
                page_id="page-1.png",
                raw_config={},
                action="local-advanced-preview",
            ))

            newer_blank = np.full((120, 120, 3), 177, dtype=np.uint8)
            Image.fromarray(newer_blank).save(cache_dir / "inpainted.png")
            with self.assertRaisesRegex(ValueError, "当前空页已发生变化"):
                asyncio.run(engine.advanced_erase_page(
                    project_id="project-a",
                    session=session,
                    page_id="page-1.png",
                    raw_config={},
                    action="local-advanced-apply",
                    attempt_id=preview["advanced_erase"]["attempt_id"],
                ))

            unchanged = np.array(Image.open(cache_dir / "inpainted.png").convert("RGB"))
            self.assertTrue(np.array_equal(unchanged, newer_blank))

    def test_local_advanced_erase_includes_high_confidence_detector_only_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            source = np.full((140, 140, 3), 238, dtype=np.uint8)
            source[36:50, 34:48] = 12
            source[88:104, 92:108] = 12
            Image.fromarray(source).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }
            cache_dir = engine._session_page_cache_dir(session, "project-a", "page-1.png")
            cache_dir.mkdir(parents=True)
            Image.fromarray(source).save(cache_dir / "inpainted.png")
            (cache_dir / "regions.json").write_text(json.dumps([
                {
                    "lines": [[[28, 30], [54, 30], [54, 56], [28, 56]]],
                    "texts": ["对白"],
                    "font_size": 16,
                    "angle": 0,
                    "translation": "",
                }
            ]), encoding="utf-8")
            raw_mask = np.zeros(source.shape[:2], dtype=np.uint8)
            raw_mask[36:50, 34:48] = 255
            raw_mask[88:104, 92:108] = 255

            async def fake_detect(_source_rgb, **_kwargs):
                return {
                    "mask": raw_mask,
                    "textlines": [
                        {
                            "points": [[28, 30], [54, 30], [54, 56], [28, 56]],
                            "probability": 0.98,
                        },
                        {
                            "points": [[86, 82], [114, 82], [114, 110], [86, 110]],
                            "probability": 0.96,
                        },
                    ],
                }

            observed_masks: list[np.ndarray] = []

            async def fake_lama(base_rgb, erase_mask, *, device):
                observed_masks.append(erase_mask.copy())
                edited = base_rgb.copy()
                edited[erase_mask > 0] = [246, 246, 246]
                return edited

            engine.inference_backend.detect_text_mask = fake_detect
            engine._select_local_inpainting_device = lambda _use_gpu: "cpu"
            engine._run_local_lama_inpaint = fake_lama

            result = asyncio.run(engine.advanced_erase_page(
                project_id="project-a",
                session=session,
                page_id="page-1.png",
                raw_config={},
                action="local-advanced-preview",
            ))

            self.assertEqual(result["advanced_erase"]["included_region_count"], 2)
            self.assertEqual(len(observed_masks), 1)
            self.assertEqual(int(observed_masks[0][42, 40]), 255)
            self.assertEqual(int(observed_masks[0][96, 100]), 255)

    def test_local_advanced_erase_rejects_black_block_on_light_background(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            source = np.full((120, 120, 3), 238, dtype=np.uint8)
            source[50:66, 52:70] = 12
            Image.fromarray(source).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }
            cache_dir = engine._session_page_cache_dir(session, "project-a", "page-1.png")
            cache_dir.mkdir(parents=True)
            current_blank = np.full((120, 120, 3), 180, dtype=np.uint8)
            Image.fromarray(current_blank).save(cache_dir / "inpainted.png")
            (cache_dir / "regions.json").write_text(json.dumps([
                {
                    "lines": [[[46, 44], [76, 44], [76, 72], [46, 72]]],
                    "texts": ["文字"],
                    "font_size": 18,
                    "angle": 0,
                    "translation": "",
                }
            ]), encoding="utf-8")

            async def fake_detect(_source_rgb, **_kwargs):
                return {"mask": np.zeros(source.shape[:2], dtype=np.uint8), "textlines": []}

            async def fake_lama(base_rgb, erase_mask, *, device):
                edited = base_rgb.copy()
                edited[erase_mask > 0] = [0, 0, 0]
                return edited

            engine.inference_backend.detect_text_mask = fake_detect
            engine._select_local_inpainting_device = lambda _use_gpu: "cpu"
            engine._run_local_lama_inpaint = fake_lama

            with self.assertRaisesRegex(RuntimeError, "异常黑块"):
                asyncio.run(engine.advanced_erase_page(
                    project_id="project-a",
                    session=session,
                    page_id="page-1.png",
                    raw_config={},
                    action="local-advanced-preview",
                ))

            unchanged_blank = np.array(Image.open(cache_dir / "inpainted.png").convert("RGB"))
            self.assertTrue(np.array_equal(unchanged_blank, current_blank))

    def test_local_advanced_erase_protects_long_panel_border_from_detector_mask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            source = np.full((160, 160, 3), 238, dtype=np.uint8)
            source[29:32, 10:150] = 8
            source[66:82, 68:84] = 8
            Image.fromarray(source).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }
            cache_dir = engine._session_page_cache_dir(session, "project-a", "page-1.png")
            cache_dir.mkdir(parents=True)
            Image.fromarray(source).save(cache_dir / "inpainted.png")
            (cache_dir / "regions.json").write_text(json.dumps([
                {
                    "lines": [[[62, 60], [90, 60], [90, 88], [62, 88]]],
                    "texts": ["对白"],
                    "font_size": 16,
                    "angle": 0,
                    "translation": "",
                }
            ]), encoding="utf-8")
            raw_mask = np.zeros(source.shape[:2], dtype=np.uint8)
            raw_mask[29:32, 10:150] = 255
            raw_mask[66:82, 68:84] = 255

            async def fake_detect(_source_rgb, **_kwargs):
                return {
                    "mask": raw_mask,
                    "textlines": [
                        {
                            "points": [[8, 24], [152, 24], [152, 37], [8, 37]],
                            "probability": 0.98,
                        },
                        {
                            "points": [[62, 60], [90, 60], [90, 88], [62, 88]],
                            "probability": 0.98,
                        },
                    ],
                }

            observed_masks: list[np.ndarray] = []

            async def fake_lama(base_rgb, erase_mask, *, device):
                observed_masks.append(erase_mask.copy())
                edited = base_rgb.copy()
                edited[erase_mask > 0] = [246, 246, 246]
                return edited

            engine.inference_backend.detect_text_mask = fake_detect
            engine._select_local_inpainting_device = lambda _use_gpu: "cpu"
            engine._run_local_lama_inpaint = fake_lama

            asyncio.run(engine.advanced_erase_page(
                project_id="project-a",
                session=session,
                page_id="page-1.png",
                raw_config={},
                action="local-advanced-preview",
            ))

            self.assertEqual(len(observed_masks), 1)
            self.assertEqual(int(observed_masks[0][30, 80]), 0)
            self.assertEqual(int(observed_masks[0][74, 76]), 255)

    def test_advanced_erase_allowed_mask_stays_close_to_text_inside_white_bubble(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((240, 240, 3), 128, dtype=np.uint8)
            cv2.ellipse(source, (120, 120), (30, 48), 0, 0, 360, (255, 255, 255), -1)
            cv2.ellipse(source, (120, 120), (30, 48), 0, 0, 360, (0, 0, 0), 2)
            source[110:122, 116:124] = 0
            region = type("Region", (), {})()
            region.xyxy = [116, 108, 124, 126]
            region.font_size = 12

            mask = engine._build_advanced_erase_region_container_mask(source, region)

            self.assertEqual(int(mask[80, 120]), 0)
            self.assertGreater(int(mask[116, 120]), 0)
            self.assertEqual(int(mask[5, 5]), 0)

    def test_advanced_erase_allowed_mask_stays_close_to_decorative_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((240, 240, 3), 112, dtype=np.uint8)
            frame = np.array([[92, 60], [150, 58], [154, 138], [96, 142], [90, 112]], dtype=np.int32)
            cv2.polylines(source, [frame], isClosed=True, color=(245, 245, 245), thickness=3)
            source[90:106, 116:126] = 245
            region = type("Region", (), {})()
            region.xyxy = [116, 88, 126, 108]
            region.font_size = 14
            region.font_style = "sfx"

            mask = engine._build_advanced_erase_region_container_mask(source, region)

            self.assertEqual(int(mask[68, 100]), 0)
            self.assertEqual(int(mask[130, 144]), 0)
            self.assertGreater(int(mask[98, 120]), 0)
            self.assertEqual(int(mask[8, 8]), 0)

    def test_advanced_erase_line_art_container_requires_decorative_style(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((240, 240, 3), 112, dtype=np.uint8)
            frame = np.array([[92, 60], [150, 58], [154, 138], [96, 142], [90, 112]], dtype=np.int32)
            cv2.polylines(source, [frame], isClosed=True, color=(245, 245, 245), thickness=3)
            source[90:106, 116:126] = 245
            region = type("Region", (), {})()
            region.xyxy = [116, 88, 126, 108]
            region.font_size = 14
            region.font_style = "gothic"

            mask = engine._build_advanced_erase_region_container_mask(source, region)

            self.assertEqual(int(mask[68, 100]), 0)
            self.assertGreater(int(mask[98, 120]), 0)

    def test_advanced_erase_overbroad_allowed_mask_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            mask = np.zeros((100, 100), dtype=np.uint8)
            mask[:27, :] = 255

            self.assertTrue(engine._advanced_erase_allowed_mask_is_overbroad(mask))

    def test_advanced_erase_never_falls_back_to_an_unbounded_page_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            overbroad_mask = np.full((100, 100), 255, dtype=np.uint8)
            raw_diff_mask = np.full((100, 100), 255, dtype=np.uint8)

            with self.assertRaisesRegex(RuntimeError, "文字区域.*过大"):
                engine._select_advanced_erase_allowed_mask(overbroad_mask)
            with self.assertRaisesRegex(RuntimeError, "受约束的文字区域"):
                engine._advanced_erase_final_mask(raw_diff_mask, None)

    def test_advanced_erase_traditional_backup_is_written_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            cache_dir = Path(tmp) / "cache" / "page-1"
            cache_dir.mkdir(parents=True)
            first_base = np.full((8, 8, 3), 240, dtype=np.uint8)
            second_base = np.full((8, 8, 3), 32, dtype=np.uint8)
            cv2.imwrite(str(cache_dir / "inpainted.png"), first_base)

            backup_path = engine._ensure_advanced_erase_traditional_backup(cache_dir)
            cv2.imwrite(str(cache_dir / "inpainted.png"), second_base)
            same_backup_path = engine._ensure_advanced_erase_traditional_backup(cache_dir)

            self.assertEqual(backup_path, same_backup_path)
            backup_bgr = cv2.imread(str(backup_path), cv2.IMREAD_COLOR)
            self.assertIsNotNone(backup_bgr)
            self.assertEqual(int(backup_bgr[0, 0, 0]), 240)

    def test_advanced_erase_config_is_independent_from_image_cleanup_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            config = engine.normalize_user_config({
                "image_cleanup_mode": "off",
                "image_cleanup_api_key": "old-cleanup-key",
                "advanced_erase_provider": "volcengine-ark",
                "advanced_erase_base_url": "https://ark.example.com/api/v3",
                "advanced_erase_model": "custom-seedream-model",
                "advanced_erase_api_key": "advanced-key",
                "advanced_erase_timeout_seconds": 12,
            })
            sanitized = engine._sanitize_config_for_storage(config)

            self.assertEqual(config["image_cleanup_mode"], "off")
            self.assertEqual(config["advanced_erase_provider"], "volcengine-ark")
            self.assertEqual(config["advanced_erase_base_url"], "https://ark.example.com/api/v3")
            self.assertEqual(config["advanced_erase_model"], "custom-seedream-model")
            self.assertEqual(config["advanced_erase_api_key"], "advanced-key")
            self.assertEqual(config["advanced_erase_timeout_seconds"], 30)
            self.assertEqual(sanitized["advanced_erase_api_key"], "")
            self.assertEqual(sanitized["image_cleanup_api_key"], "")

    def test_persisted_settings_redact_and_preserve_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            saved = engine.save_persisted_settings({
                "translator": "gemini",
                "api_key": "top-secret",
            })
            self.assertEqual(saved["api_key"], "")
            self.assertTrue(saved["configured_secrets"]["api_key"])
            self.assertEqual(engine.paths.load_settings()["api_key"], "top-secret")

            updated = engine.save_persisted_settings({
                "target_lang": "ENG",
                "api_key": "",
            })
            self.assertEqual(updated["api_key"], "")
            self.assertTrue(updated["configured_secrets"]["api_key"])
            self.assertEqual(engine.paths.load_settings()["api_key"], "top-secret")
            self.assertEqual(engine.normalize_user_config({})["api_key"], "top-secret")

    def test_persisted_style_font_mappings_round_trip_in_public_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / "backend"
            bundled_font_dir = root / "fonts" / "system"
            custom_font_dir = root / "fonts" / "custom"
            bundled_font_dir.mkdir(parents=True)
            custom_font_dir.mkdir(parents=True)
            (bundled_font_dir / "SourceHanSansSC-Regular-2.otf").write_bytes(b"bundled-font")
            custom_font = custom_font_dir / "CustomDialogue.otf"
            custom_font.write_bytes(b"custom-font")
            engine = TranslatorEngine(base_dir, app_paths=make_test_paths(root))
            custom_font_key = f"project:{custom_font.name}"

            saved = engine.save_persisted_settings({
                "style_font_gothic_key": custom_font_key,
            })
            reloaded = engine.load_persisted_settings()

            self.assertEqual(saved["style_font_gothic_key"], custom_font_key)
            self.assertEqual(reloaded["style_font_gothic_key"], custom_font_key)
            self.assertEqual(reloaded["style_font_keys"]["gothic"], custom_font_key)

    def test_openai_compatible_settings_survive_save_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            engine.save_persisted_settings({
                "translator": "openai-compatible",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "example-model",
                "api_key": "top-secret",
            })
            reloaded = engine.load_persisted_settings()

            self.assertEqual(reloaded["translator"], "openai-compatible")
            self.assertEqual(reloaded["selected_translator"], "openai-compatible")
            self.assertEqual(reloaded["openai_base_url"], "https://api.example.com/v1")
            self.assertEqual(reloaded["openai_model"], "example-model")
            self.assertTrue(reloaded["configured_secrets"]["api_key"])

    def test_persisted_settings_require_explicit_secret_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            engine.save_persisted_settings({"api_key": "top-secret"})

            cleared = engine.save_persisted_settings({
                "api_key": "",
                "_clear_secrets": ["api_key"],
            })

            self.assertFalse(cleared["configured_secrets"]["api_key"])
            self.assertEqual(engine.paths.load_settings()["api_key"], "")

    @unittest.skipIf(os.name == "nt", "POSIX file modes are not portable to Windows")
    def test_persisted_settings_file_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            engine.save_persisted_settings({"api_key": "top-secret"})

            mode = engine.paths.settings_path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_default_font_mapping_uses_bundled_font(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / "backend"
            system_font_dir = root / "fonts" / "system"
            system_font_dir.mkdir(parents=True)
            test_font = system_font_dir / "SourceHanSansSC-Regular-2.otf"
            test_font.write_bytes(b"test-font")
            engine = TranslatorEngine(base_dir, app_paths=make_test_paths(root))

            config = engine.normalize_user_config({})

            self.assertEqual(config["font_style_mode"], "auto-map")
            self.assertEqual(config["font_key"], engine.DEFAULT_FONT_KEY)
            for style in engine.STYLE_BUCKETS:
                self.assertEqual(config["style_font_keys"][style], engine.DEFAULT_FONT_KEY)
                self.assertTrue(config["style_font_paths"][style].endswith("SourceHanSansSC-Regular-2.otf"))

    def test_font_mapping_keeps_bundled_style_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / "backend"
            bundled_font_dir = root / "fonts" / "system"
            custom_font_dir = root / "fonts" / "custom"
            bundled_font_dir.mkdir(parents=True)
            custom_font_dir.mkdir(parents=True)
            (bundled_font_dir / "SourceHanSansSC-Regular-2.otf").write_bytes(b"bundled-font")
            custom_font = custom_font_dir / "CustomDialogue.otf"
            custom_font.write_bytes(b"bundled-font")
            engine = TranslatorEngine(base_dir, app_paths=make_test_paths(root))

            config = engine.normalize_user_config({
                "style_font_gothic_key": f"project:{custom_font.name}",
                "style_font_sfx_key": "",
            })

            self.assertEqual(config["style_font_keys"]["gothic"], f"project:{custom_font.name}")
            self.assertEqual(config["style_font_keys"]["sfx"], engine.DEFAULT_FONT_KEY)
            self.assertEqual(Path(config["style_font_paths"]["gothic"]).name, custom_font.name)

    def test_font_mapping_rejects_arbitrary_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / "backend"
            system_font_dir = root / "fonts" / "system"
            system_font_dir.mkdir(parents=True)
            default_font = system_font_dir / "SourceHanSansSC-Regular-2.otf"
            default_font.write_bytes(b"bundled-font")
            outside_font = root / "outside.otf"
            outside_font.write_bytes(b"outside-font")
            engine = TranslatorEngine(base_dir, app_paths=make_test_paths(root))

            config = engine.normalize_user_config({
                "font_key": str(outside_font),
                "style_font_gothic_key": f"project:../{outside_font.name}",
            })

            self.assertEqual(config["font_key"], engine.DEFAULT_FONT_KEY)
            self.assertEqual(Path(config["font_path"]).name, default_font.name)
            self.assertEqual(config["style_font_keys"]["gothic"], engine.DEFAULT_FONT_KEY)
            self.assertEqual(Path(config["style_font_paths"]["gothic"]).name, default_font.name)

    def test_recent_project_prefixed_preset_font_key_moves_back_to_system(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / "backend"
            system_font_dir = root / "fonts" / "system"
            system_font_dir.mkdir(parents=True)
            preset = system_font_dir / "SourceHanSansSC-Medium-2.otf"
            preset.write_bytes(b"bundled-font")
            engine = TranslatorEngine(base_dir, app_paths=make_test_paths(root))

            config = engine.normalize_user_config({
                "font_key": f"project:{preset.name}",
                "style_font_gothic_key": f"project:{preset.name}",
            })

            expected_key = f"system:{preset.name}"
            self.assertEqual(config["font_key"], expected_key)
            self.assertEqual(config["style_font_keys"]["gothic"], expected_key)
            self.assertEqual(Path(config["font_path"]).name, preset.name)

    def test_advanced_erase_rejection_saves_debug_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            Image.new("RGB", (80, 80), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }

            class FakeClient:
                async def remove_text(self, *_args, **_kwargs):
                    return np.full((80, 80, 3), 120, dtype=np.uint8)

            original_factory = translator_module.create_image_cleanup_client
            translator_module.create_image_cleanup_client = lambda **_kwargs: FakeClient()
            try:
                with self.assertRaisesRegex(RuntimeError, "调试文件已保存到"):
                    asyncio.run(engine.advanced_erase_page(
                        project_id="project-a",
                        session=session,
                        page_id="page-1.png",
                        raw_config={
                            "advanced_erase_provider": "volcengine-ark",
                            "advanced_erase_base_url": "https://ark.example.com/api/v3/images/generations",
                            "advanced_erase_model": "custom-seedream-model",
                            "advanced_erase_api_key": "secret",
                        },
                    ))
            finally:
                translator_module.create_image_cleanup_client = original_factory

            attempt_dir = engine._advanced_erase_attempt_dir(
                engine._session_page_cache_dir(session, "project-a", "page-1.png")
            )
            input_images = list(attempt_dir.glob("*.input.png"))
            seedream_outputs = list(attempt_dir.glob("*.seedream.png"))
            diff_outputs = list(attempt_dir.glob("*.diff.png"))
            mask_outputs = list(attempt_dir.glob("*.mask.png"))
            metadata_outputs = list(attempt_dir.glob("*.json"))
            self.assertEqual(len(input_images), 1)
            self.assertEqual(len(seedream_outputs), 1)
            self.assertEqual(len(diff_outputs), 1)
            self.assertEqual(len(mask_outputs), 1)
            self.assertEqual(len(metadata_outputs), 1)
            metadata = json.loads(metadata_outputs[0].read_text(encoding="utf-8"))
            self.assertTrue(metadata["rejected"])
            self.assertEqual(metadata["changed_ratio"], 0.0)
            self.assertGreater(metadata["raw_changed_ratio"], engine.ADVANCED_ERASE_MAX_CHANGED_RATIO)
            self.assertEqual(metadata["mask_mode"], "rejected_region_constraint")
            self.assertIn("没有找到可约束的文字区域", metadata["error"])
            self.assertEqual(Path(metadata["input_image"]).name, input_images[0].name)
            self.assertEqual(Path(metadata["seedream_output"]).name, seedream_outputs[0].name)
            self.assertEqual(Path(metadata["diff_mask"]).name, diff_outputs[0].name)
            self.assertEqual(Path(metadata["final_mask"]).name, mask_outputs[0].name)

    def test_advanced_erase_overbroad_region_rejection_saves_allowed_mask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            Image.new("RGB", (80, 80), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }

            class FakeClient:
                async def remove_text(self, *_args, **_kwargs):
                    return np.full((80, 80, 3), 120, dtype=np.uint8)

            overbroad_mask = np.full((80, 80), 255, dtype=np.uint8)
            engine._build_advanced_erase_allowed_mask = (  # type: ignore[method-assign]
                lambda *_args, **_kwargs: (overbroad_mask, 1)
            )
            original_factory = translator_module.create_image_cleanup_client
            translator_module.create_image_cleanup_client = lambda **_kwargs: FakeClient()
            try:
                with self.assertRaisesRegex(RuntimeError, "文字区域异常过大"):
                    asyncio.run(engine.advanced_erase_page(
                        project_id="project-a",
                        session=session,
                        page_id="page-1.png",
                        raw_config={
                            "advanced_erase_provider": "volcengine-ark",
                            "advanced_erase_base_url": "https://ark.example.com/api/v3/images/generations",
                            "advanced_erase_model": "custom-seedream-model",
                            "advanced_erase_api_key": "secret",
                        },
                    ))
            finally:
                translator_module.create_image_cleanup_client = original_factory

            attempt_dir = engine._advanced_erase_attempt_dir(
                engine._session_page_cache_dir(session, "project-a", "page-1.png")
            )
            allowed_outputs = list(attempt_dir.glob("*.allowed.png"))
            metadata_outputs = list(attempt_dir.glob("*.json"))
            self.assertEqual(len(allowed_outputs), 1)
            self.assertEqual(len(metadata_outputs), 1)
            metadata = json.loads(metadata_outputs[0].read_text(encoding="utf-8"))
            self.assertTrue(metadata["rejected"])
            self.assertEqual(Path(metadata["allowed_mask"]).name, allowed_outputs[0].name)


if __name__ == "__main__":
    unittest.main()
