from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import logging
import os
import re
import shutil
import sys
import threading
import uuid
from concurrent.futures import Future as ConcurrentFuture
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    """A domain request for one upstream image-inference run."""

    source_dir: Path = field(repr=False)
    output_dir: Path = field(repr=False)
    config_path: Path = field(repr=False)
    log_path: Path = field(repr=False)
    model_dir: Path = field(repr=False)
    required_outputs: tuple[Path, ...] = field(default=(), repr=False)
    use_gpu: bool = False
    detection_only: bool = False
    font_path: str = field(default="", repr=False)
    runtime_key: str = "default"
    environment: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)
    progress: Callable[["InferenceProgress"], Awaitable[None]] | None = field(
        default=None,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True, slots=True)
class InferenceResult:
    outputs: tuple[Path, ...] = field(repr=False)
    exit_code: int
    runtime_reused: bool = False


@dataclass(frozen=True, slots=True)
class InferenceProgress:
    step: str
    message: str = ""


class InferenceBackendError(RuntimeError):
    category = "inference"


class InferenceMalformedOutputError(InferenceBackendError):
    category = "malformed_output"


class InferenceRuntimeError(InferenceBackendError):
    category = "runtime_unavailable"


class InferenceUpstreamError(InferenceBackendError):
    category = "upstream_failure"

    def __init__(self, message: str, *, exit_code: int, classification: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.classification = classification


@runtime_checkable
class InferenceBackend(Protocol):
    async def run(self, request: InferenceRequest) -> InferenceResult:
        """Run inference without exposing subprocess or vendor-module details."""

    async def erase_selection(
        self,
        base_image: Any,
        selection_mask: Any,
        *,
        model_dir: Path,
        device: str,
        inpainting_size: int,
    ) -> Any: ...

    async def recognize_region(
        self,
        source_image: Any,
        bbox: list[int],
        *,
        device: str,
    ) -> dict[str, Any]: ...

    async def translate_texts(
        self,
        texts: list[str],
        *,
        translator_name: str,
        target_lang: str,
        device: str,
        environment: Mapping[str, str],
    ) -> tuple[str, ...]: ...

    def create_text_region(self, **attributes: Any) -> Any: ...

    def build_command(self, request: InferenceRequest) -> tuple[str, ...]: ...

    def prepare_runtime_patches(self) -> bool: ...

    def model_download_notice(self, log_path: Path) -> str: ...

    def runtime_contract_notice(self, log_path: Path) -> str: ...

    def format_failure(
        self,
        log_path: Path,
        stage_label: str = "manga-image-translator 执行",
    ) -> str: ...

    def pre_render_failure(self, log_path: Path, stage_label: str) -> str: ...


ProcessFactory = Callable[..., Awaitable[Any]]
RuntimePreparer = Callable[[InferenceRequest], bool | None | Awaitable[bool | None]]
logger = logging.getLogger("manga_translator.inference")


class UpstreamInferenceBackend:
    """Production adapter for the fixed manga-image-translator runtime."""

    def __init__(
        self,
        base_dir: Path,
        *,
        process_factory: ProcessFactory | None = None,
        runtime_preparer: RuntimePreparer | None = None,
        poll_interval_seconds: float = 1.0,
        termination_grace_seconds: float = 5.0,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._upstream_dir = self._base_dir / "manga-image-translator"
        self._process_factory = process_factory or asyncio.create_subprocess_exec
        self._runtime_preparer = runtime_preparer or self._prepare_runtime
        self._poll_interval_seconds = poll_interval_seconds
        self._termination_grace_seconds = termination_grace_seconds
        self._ready_runtime_keys: set[tuple[str, str, bool]] = set()
        self._runtime_futures: dict[tuple[str, str, bool], ConcurrentFuture[None]] = {}
        self._runtime_lock = threading.Lock()

    async def run(self, request: InferenceRequest) -> InferenceResult:
        runtime_reused = await self._ensure_runtime_ready(request)
        try:
            request.log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            raise InferenceRuntimeError(
                "无法准备推理日志，请检查应用数据目录。"
            ) from None
        command = self.build_command(request)
        environment = os.environ.copy()
        environment.update({str(key): str(value) for key, value in request.environment.items()})
        try:
            staged_outputs = self._stage_required_outputs(request.required_outputs)
        except OSError:
            raise InferenceRuntimeError(
                "无法安全准备推理输出，请检查应用数据目录。"
            ) from None

        process: Any | None = None
        wait_task: asyncio.Task[Any] | None = None
        failure_phase = "log_open"
        try:
            with request.log_path.open("wb") as log_file:
                failure_phase = "launch"
                candidate = await self._process_factory(
                    *command,
                    cwd=str(self._upstream_dir),
                    env=environment,
                    stdout=log_file,
                    stderr=log_file,
                )
                if not all(
                    callable(getattr(candidate, name, None))
                    for name in ("wait", "terminate", "kill")
                ) or not hasattr(candidate, "returncode"):
                    raise InferenceMalformedOutputError(
                        "上游推理返回了无法识别的执行结果。"
                    )
                process = candidate
                failure_phase = "process_wait"
                wait_task = asyncio.create_task(process.wait())
                last_notices: set[tuple[str, str]] = set()
                while not wait_task.done():
                    if request.progress is not None:
                        await request.progress(InferenceProgress(step="running"))
                        for notice in self.log_notices(request.log_path):
                            key = (notice.step, notice.message)
                            if key not in last_notices:
                                last_notices.add(key)
                                await request.progress(notice)
                    await asyncio.wait(
                        (wait_task,),
                        timeout=self._poll_interval_seconds,
                    )
                returncode = await wait_task
        except asyncio.CancelledError:
            await self._cleanup_started_process(process, wait_task)
            try:
                self._restore_required_outputs(staged_outputs)
            except OSError:
                logger.error(
                    "Inference cancellation cleanup could not restore prior outputs"
                )
                raise InferenceRuntimeError(
                    "推理已取消，但无法恢复先前输出；恢复副本已保留。"
                ) from None
            raise
        except InferenceBackendError:
            await self._cleanup_started_process(process, wait_task)
            try:
                self._restore_required_outputs(staged_outputs)
            except OSError:
                logger.error("Inference failure cleanup could not restore prior outputs")
                raise InferenceRuntimeError(
                    "推理失败，且无法恢复先前输出；恢复副本已保留。"
                ) from None
            raise
        except Exception:
            await self._cleanup_started_process(process, wait_task)
            try:
                self._restore_required_outputs(staged_outputs)
            except OSError:
                logger.error("Inference failure cleanup could not restore prior outputs")
                raise InferenceRuntimeError(
                    "推理失败，且无法恢复先前输出；恢复副本已保留。"
                ) from None
            if failure_phase == "log_open":
                raise InferenceRuntimeError(
                    "无法打开推理日志，请检查应用数据目录。"
                ) from None
            raise InferenceUpstreamError(
                "上游推理进程无法正常执行，请查看任务日志。",
                exit_code=-1,
                classification=failure_phase,
            ) from None
        except BaseException:
            await self._cleanup_started_process(process, wait_task)
            try:
                self._restore_required_outputs(staged_outputs)
            except OSError:
                logger.error("Inference failure cleanup could not restore prior outputs")
            raise

        try:
            try:
                process_returncode = process.returncode
            except Exception:
                raise InferenceMalformedOutputError(
                    "上游推理返回了无效的退出状态。"
                ) from None
            if (
                type(returncode) is not int
                or type(process_returncode) is not int
                or returncode != process_returncode
            ):
                raise InferenceMalformedOutputError(
                    "上游推理返回了无效的退出状态。"
                )
            normalized_returncode = returncode
            if normalized_returncode != 0:
                self._summarize_log(request.log_path, failed=True)
                classification = self._classify_failure(request.log_path)
                raise InferenceUpstreamError(
                    f"上游推理执行失败（退出码 {normalized_returncode}，分类：{classification}）。请查看任务日志。",
                    exit_code=normalized_returncode,
                    classification=classification,
                )

            self._summarize_log(request.log_path, failed=False)
            outputs = tuple(path for path in request.required_outputs if path.is_file())
            if len(outputs) != len(request.required_outputs):
                missing_count = len(request.required_outputs) - len(outputs)
                raise InferenceMalformedOutputError(
                    f"上游推理声明成功，但缺少 {missing_count} 个必需输出。"
                )
        except BaseException:
            try:
                self._restore_required_outputs(staged_outputs)
            except OSError:
                logger.error("Inference failure cleanup could not restore prior outputs")
                raise InferenceRuntimeError(
                    "推理失败，且无法恢复先前输出；恢复副本已保留。"
                ) from None
            raise
        else:
            try:
                self._discard_staged_outputs(staged_outputs)
            except OSError:
                logger.error("Inference succeeded but prior-output backup cleanup failed")
                raise InferenceRuntimeError(
                    "推理输出已生成，但无法清理先前输出的恢复副本。"
                ) from None
            return InferenceResult(
                outputs=outputs,
                exit_code=normalized_returncode,
                runtime_reused=runtime_reused,
            )

    @classmethod
    def _stage_required_outputs(
        cls,
        required_outputs: tuple[Path, ...],
    ) -> tuple[tuple[Path, Path | None], ...]:
        staged: list[tuple[Path, Path | None]] = []
        seen: set[Path] = set()
        try:
            for raw_path in required_outputs:
                path = Path(raw_path)
                if path in seen:
                    continue
                seen.add(path)
                backup: Path | None = None
                if path.exists() or path.is_symlink():
                    backup = path.with_name(
                        f".{path.name}.inference-backup-{uuid.uuid4().hex}"
                    )
                    os.replace(path, backup)
                staged.append((path, backup))
        except OSError:
            cls._restore_required_outputs(tuple(staged))
            raise
        return tuple(staged)

    @classmethod
    def _restore_required_outputs(
        cls,
        staged: tuple[tuple[Path, Path | None], ...],
    ) -> None:
        failed = False
        for path, backup in reversed(staged):
            try:
                cls._remove_output_path(path)
            except OSError:
                failed = True
            if backup is not None:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(backup, path)
                except OSError:
                    failed = True
        if failed:
            raise OSError("required output restoration failed")

    @classmethod
    def _discard_staged_outputs(
        cls,
        staged: tuple[tuple[Path, Path | None], ...],
    ) -> None:
        failed = False
        for _path, backup in staged:
            if backup is not None:
                try:
                    cls._remove_output_path(backup)
                except OSError:
                    failed = True
        if failed:
            raise OSError("required output backup cleanup failed")

    async def _cleanup_started_process(
        self,
        process: Any | None,
        wait_task: asyncio.Task[Any] | None,
    ) -> None:
        if process is None:
            return
        try:
            if process.returncode is not None:
                return
        except BaseException:
            pass
        try:
            process.terminate()
        except ProcessLookupError:
            return
        except BaseException:
            pass
        try:
            if wait_task is not None:
                await asyncio.wait_for(
                    asyncio.shield(wait_task),
                    timeout=self._termination_grace_seconds,
                )
            else:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=self._termination_grace_seconds,
                )
            return
        except BaseException:
            pass
        try:
            process.kill()
        except ProcessLookupError:
            return
        except BaseException:
            return
        try:
            if wait_task is not None:
                await asyncio.shield(wait_task)
            else:
                await process.wait()
        except BaseException:
            pass

    @staticmethod
    def _remove_output_path(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    @staticmethod
    def _classify_failure(log_path: Path) -> str:
        try:
            content = log_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return "process"
        if "ChunkedEncodingError" in content or "IncompleteRead(" in content:
            return "model_download"
        if "Downloading models into" in content and "Traceback" in content:
            return "model_preparation"
        if "Traceback" in content:
            return "runtime"
        return "process"

    def build_command(self, request: InferenceRequest) -> tuple[str, ...]:
        command = [
            sys.executable,
            "-m",
            "manga_translator",
            "local",
            "-i",
            str(request.source_dir),
            "-o",
            str(request.output_dir),
            "--overwrite",
            "--config-file",
            str(request.config_path),
            "--model-dir",
            str(request.model_dir),
        ]
        if request.font_path:
            command.extend(("--font-path", request.font_path))
        if request.detection_only:
            command.append("--prep-manual")
        if request.use_gpu:
            command.append("--use-gpu")
        return tuple(command)

    def log_notices(self, log_path: Path) -> tuple[InferenceProgress, ...]:
        content = self._read_log_tail(log_path)
        notices: list[InferenceProgress] = []
        downloads = re.findall(r'-- Downloading:\s*"([^"]+)"', content)
        if downloads:
            filename = Path(downloads[-1].split("?", 1)[0]).name or "模型文件"
            notices.append(
                InferenceProgress(
                    step="model",
                    message=f"首次使用正在下载模型：{filename}。下载失败时会自动切换备用源。",
                )
            )
        contracts = re.findall(
            r"\[RuntimeContract\]\s+device=(\S+)\s+model_dir=(.+)",
            content,
        )
        if contracts:
            device, _model_dir = contracts[-1]
            device_label = {
                "cuda": "NVIDIA CUDA",
                "mps": "Apple Metal",
                "cpu": "CPU",
            }.get(device.lower(), device)
            notices.append(
                InferenceProgress(
                    step="model",
                    message=f"推理运行时已确认：{device_label}，模型将写入应用模型目录。",
                )
            )
        return tuple(notices)

    def model_download_notice(self, log_path: Path) -> str:
        return next(
            (notice.message for notice in self.log_notices(log_path) if "下载模型" in notice.message),
            "",
        )

    def runtime_contract_notice(self, log_path: Path) -> str:
        return next(
            (notice.message for notice in self.log_notices(log_path) if "推理运行时" in notice.message),
            "",
        )

    def format_failure(self, log_path: Path, stage_label: str = "manga-image-translator 执行") -> str:
        classification = self._classify_failure(log_path)
        messages = {
            "model_download": f"{stage_label}未完成：模型下载连接中断，请重试。",
            "model_preparation": f"{stage_label}未完成：模型准备阶段发生异常。",
            "runtime": f"{stage_label}未完成：推理运行时在生成结果前发生异常。",
            "process": f"{stage_label}失败，请查看任务日志。",
        }
        return messages[classification]

    def pre_render_failure(self, log_path: Path, stage_label: str) -> str:
        classification = self._classify_failure(log_path)
        if classification == "process":
            return ""
        return self.format_failure(log_path, stage_label)

    def prepare_runtime_patches(self) -> bool:
        if os.getenv("APP_RUNTIME_PATCHES_PREPARED") == "1":
            return True
        from patch_pydensecrf import patch_mask_refinement

        return bool(patch_mask_refinement())

    async def erase_selection(
        self,
        base_image: Any,
        selection_mask: Any,
        *,
        model_dir: Path,
        device: str,
        inpainting_size: int,
    ) -> Any:
        """Erase one selected image area with the fixed local inpainter."""
        self._ensure_vendor_import_path()
        self.prepare_runtime_patches()
        from manga_translator.config import Inpainter, InpainterConfig, InpaintPrecision
        from manga_translator.inpainting import dispatch as dispatch_inpainting
        from manga_translator.utils import ModelWrapper

        ModelWrapper._MODEL_DIR = str(model_dir)
        config = InpainterConfig(
            inpainter=Inpainter.lama_large,
            inpainting_size=inpainting_size,
            inpainting_precision=InpaintPrecision.bf16,
        )
        return await dispatch_inpainting(
            Inpainter.lama_large,
            base_image,
            selection_mask,
            config,
            inpainting_size,
            device,
            False,
        )

    async def recognize_region(
        self,
        source_image: Any,
        bbox: list[int],
        *,
        device: str,
    ) -> dict[str, Any]:
        """Recognize one rectangular region and return domain text attributes."""
        self._ensure_vendor_import_path()
        import numpy as np
        from manga_translator.config import Ocr, OcrConfig
        from manga_translator.ocr import dispatch as dispatch_ocr
        from manga_translator.utils import Quadrilateral

        points = np.array(
            [
                [bbox[0], bbox[1]],
                [bbox[2], bbox[1]],
                [bbox[2], bbox[3]],
                [bbox[0], bbox[3]],
            ],
            dtype=np.int32,
        )
        recognized = await dispatch_ocr(
            Ocr.ocr48px,
            source_image,
            [Quadrilateral(points, "", 1.0)],
            OcrConfig(use_mocr_merge=True, ocr=Ocr.ocr48px),
            device,
            False,
        )
        if not recognized:
            return {}
        region = recognized[0]
        return {
            "source_text": str(getattr(region, "text", "") or "").strip(),
            "direction": str(getattr(region, "direction", "") or ""),
            "font_size": float(getattr(region, "font_size", 0) or 0) or None,
            "fg_color": tuple(int(value) for value in getattr(region, "fg_colors", (0, 0, 0))),
            "bg_color": tuple(int(value) for value in getattr(region, "bg_colors", (255, 255, 255))),
        }

    async def translate_texts(
        self,
        texts: list[str],
        *,
        translator_name: str,
        target_lang: str,
        device: str,
        environment: Mapping[str, str],
    ) -> tuple[str, ...]:
        """Run the fixed upstream translation dispatcher without exposing its modules."""
        self._ensure_vendor_import_path()
        self._reload_vendor_translator_modules()
        from manga_translator.config import Translator, TranslatorConfig
        from manga_translator.translators import dispatch as dispatch_translation
        from manga_translator.translators import unload as unload_translator

        translator_key = Translator[translator_name]
        translator_config = TranslatorConfig(
            translator=translator_key,
            target_lang=target_lang,
        )
        with self._temporary_environment(environment):
            with contextlib.suppress(Exception):
                await unload_translator(translator_key)
            try:
                translated = await dispatch_translation(
                    translator_config.translator_gen,
                    texts,
                    translator_config=translator_config,
                    use_mtpe=False,
                    args=None,
                    device=device,
                )
            finally:
                with contextlib.suppress(Exception):
                    await unload_translator(translator_key)
        return tuple(str(item or "").strip() for item in (translated or ()))

    def create_text_region(self, **attributes: Any) -> Any:
        """Restore the fixed upstream region object from domain attributes."""
        self._ensure_vendor_import_path()
        from manga_translator.utils.textblock import TextBlock

        return TextBlock(**attributes)

    def _ensure_vendor_import_path(self) -> None:
        vendor_root = str(self._upstream_dir)
        if vendor_root not in sys.path:
            sys.path.insert(0, vendor_root)

    def _reload_vendor_translator_modules(self) -> None:
        self._ensure_vendor_import_path()
        importlib.invalidate_caches()
        for module_name in (
            "manga_translator.translators.custom_openai",
            "manga_translator.translators",
        ):
            module = sys.modules.get(module_name)
            if module is not None:
                importlib.reload(module)

    @staticmethod
    @contextmanager
    def _temporary_environment(updates: Mapping[str, str]):
        sentinel = object()
        previous = {key: os.environ.get(key, sentinel) for key in updates}
        try:
            os.environ.update({str(key): str(value) for key, value in updates.items()})
            yield
        finally:
            for key, value in previous.items():
                if value is sentinel:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = str(value)

    @staticmethod
    def _read_log_tail(log_path: Path, limit: int = 32 * 1024) -> str:
        try:
            with log_path.open("rb") as handle:
                handle.seek(0, 2)
                handle.seek(max(0, handle.tell() - limit))
                return handle.read().decode("utf-8", errors="ignore")
        except OSError:
            return ""

    @staticmethod
    def _summarize_log(log_path: Path, *, failed: bool) -> None:
        if not failed:
            logger.info("Inference task log saved")
            return
        logger.error("Inference task failed; inspect the task log for details")

    async def _ensure_runtime_ready(self, request: InferenceRequest) -> bool:
        key = (request.runtime_key, str(request.model_dir.resolve()), request.use_gpu)
        with self._runtime_lock:
            if key in self._ready_runtime_keys:
                return True
            future = self._runtime_futures.get(key)
            owner = future is None
            if owner:
                future = ConcurrentFuture()
                self._runtime_futures[key] = future
        assert future is not None
        if not owner:
            await asyncio.shield(asyncio.wrap_future(future))
            return True
        try:
            prepared = self._runtime_preparer(request)
            if inspect.isawaitable(prepared):
                prepared = await prepared
            if prepared is False:
                raise InferenceRuntimeError("推理运行时尚未准备完成。")
        except asyncio.CancelledError as exc:
            with self._runtime_lock:
                self._runtime_futures.pop(key, None)
            if not future.done():
                future.set_exception(
                    InferenceRuntimeError("推理运行时准备被取消，请重试。")
                )
            raise
        except InferenceBackendError as exc:
            with self._runtime_lock:
                self._runtime_futures.pop(key, None)
            if not future.done():
                future.set_exception(exc)
            raise
        except Exception:
            failure = InferenceRuntimeError("推理运行时准备失败，请检查安装状态。")
            with self._runtime_lock:
                self._runtime_futures.pop(key, None)
            if not future.done():
                future.set_exception(failure)
            raise failure from None
        else:
            with self._runtime_lock:
                self._ready_runtime_keys.add(key)
                self._runtime_futures.pop(key, None)
            if not future.done():
                future.set_result(None)
            return False

    @staticmethod
    def _prepare_runtime(_request: InferenceRequest) -> bool:
        if os.getenv("APP_RUNTIME_PATCHES_PREPARED") == "1":
            return True
        from patch_pydensecrf import patch_mask_refinement

        return bool(patch_mask_refinement())


__all__ = [
    "InferenceBackend",
    "InferenceBackendError",
    "InferenceMalformedOutputError",
    "InferenceProgress",
    "InferenceRequest",
    "InferenceResult",
    "InferenceRuntimeError",
    "InferenceUpstreamError",
    "UpstreamInferenceBackend",
]
