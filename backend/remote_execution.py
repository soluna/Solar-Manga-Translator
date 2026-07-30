from __future__ import annotations

import contextlib
import hashlib
import json
import mimetypes
import os
import signal
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from diagnostics_bundle import redact_diagnostics_data
from remote_diagnostics import discover_lan_ipv4_addresses
from runtime_paths import AppPaths


DEFAULT_REMOTE_EXECUTION_PORT = 8800
DEFAULT_MAX_UPLOAD_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_FILES = 10_000
MAX_ARCHIVE_EXPANDED_BYTES = 4 * 1024 * 1024 * 1024
TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled"})
VALID_JOB_STATUSES = frozenset({"queued", "running", *TERMINAL_JOB_STATUSES})


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temp_path = Path(raw_temp_path)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def _tail_text(path: Path, *, max_bytes: int = 64 * 1024) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        return handle.read().decode("utf-8", errors="replace")


def _identifier(candidate: str, *, label: str) -> str:
    normalized = str(candidate or "").strip().lower()
    if len(normalized) != 32 or any(character not in "0123456789abcdef" for character in normalized):
        raise FileNotFoundError(f"{label}不存在。")
    return normalized


class RemoteExecutionAccess:
    def __init__(self, token: str = ""):
        self._lock = threading.RLock()
        self._token_digest = b""
        if token:
            self.set_token(token)

    def set_token(self, token: str) -> None:
        normalized = str(token or "").strip()
        if not normalized:
            raise ValueError("远程任务令牌不能为空。")
        with self._lock:
            self._token_digest = hashlib.sha256(normalized.encode("utf-8")).digest()

    def is_authorized(self, candidate: str) -> bool:
        normalized = str(candidate or "").strip()
        if not normalized:
            return False
        digest = hashlib.sha256(normalized.encode("utf-8")).digest()
        with self._lock:
            return bool(self._token_digest and secrets.compare_digest(digest, self._token_digest))


class RemoteBundleStore:
    def __init__(self, root: Path, *, max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES):
        self.root = root
        self.max_upload_bytes = max(1, int(max_upload_bytes))
        self.root.mkdir(parents=True, exist_ok=True)

    def _bundle_dir(self, bundle_id: str) -> Path:
        return self.root / _identifier(bundle_id, label="上传包")

    def files_dir(self, bundle_id: str) -> Path:
        root = self._bundle_dir(bundle_id) / "files"
        if not root.is_dir():
            raise FileNotFoundError("上传包不存在。")
        return root

    def _copy_upload(self, source: BinaryIO, destination: Path) -> tuple[int, str]:
        copied = 0
        digest = hashlib.sha256()
        with destination.open("wb") as handle:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > self.max_upload_bytes:
                    raise ValueError("上传包超过允许大小。")
                digest.update(chunk)
                handle.write(chunk)
        return copied, digest.hexdigest()

    def _safe_archive_path(self, name: str) -> PurePosixPath:
        candidate = PurePosixPath(str(name or "").replace("\\", "/"))
        if candidate.is_absolute() or not candidate.parts or any(part in {"", ".", ".."} for part in candidate.parts):
            raise ValueError("压缩包包含不安全的文件路径。")
        return candidate

    def _extract_zip(self, archive_path: Path, destination: Path) -> None:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_FILES:
                raise ValueError("压缩包文件数量过多。")
            expanded_size = sum(max(0, int(member.file_size)) for member in members)
            if expanded_size > MAX_ARCHIVE_EXPANDED_BYTES:
                raise ValueError("压缩包解压后超过允许大小。")
            resolved_destination = destination.resolve()
            for member in members:
                relative = self._safe_archive_path(member.filename)
                mode = (member.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise ValueError("压缩包不能包含符号链接。")
                target = (destination / Path(*relative.parts)).resolve()
                if resolved_destination != target and resolved_destination not in target.parents:
                    raise ValueError("压缩包包含不安全的文件路径。")
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)

    def create(self, *, filename: str, source: BinaryIO) -> dict[str, Any]:
        normalized_name = Path(str(filename or "bundle.bin")).name.strip() or "bundle.bin"
        if normalized_name != str(filename or normalized_name).replace("\\", "/").split("/")[-1]:
            raise ValueError("上传文件名不合法。")
        bundle_id = uuid.uuid4().hex
        bundle_dir = self.root / bundle_id
        original_dir = bundle_dir / "original"
        files_dir = bundle_dir / "files"
        original_dir.mkdir(parents=True)
        files_dir.mkdir(parents=True)
        original_path = original_dir / normalized_name
        try:
            size_bytes, sha256 = self._copy_upload(source, original_path)
            if zipfile.is_zipfile(original_path):
                self._extract_zip(original_path, files_dir)
            else:
                shutil.copy2(original_path, files_dir / normalized_name)
            metadata = {
                "id": bundle_id,
                "filename": normalized_name,
                "size_bytes": size_bytes,
                "sha256": sha256,
                "created_at": _utc_now(),
            }
            _write_json(bundle_dir / "bundle.json", metadata)
            return self.describe(bundle_id)
        except Exception:
            shutil.rmtree(bundle_dir, ignore_errors=True)
            raise

    def _file_payload(self, bundle_id: str, path: Path) -> dict[str, Any]:
        files_dir = self.files_dir(bundle_id).resolve()
        resolved = path.resolve(strict=True)
        relative_path = resolved.relative_to(files_dir).as_posix()
        stat = resolved.stat()
        return {
            "id": hashlib.sha256(f"{bundle_id}\0{relative_path}".encode("utf-8")).hexdigest()[:24],
            "relative_path": relative_path,
            "size_bytes": stat.st_size,
            "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        }

    def list_files(self, bundle_id: str) -> list[dict[str, Any]]:
        files_dir = self.files_dir(bundle_id)
        return [
            self._file_payload(bundle_id, path)
            for path in sorted(files_dir.rglob("*"))
            if path.is_file() and not path.is_symlink()
        ]

    def describe(self, bundle_id: str) -> dict[str, Any]:
        normalized = _identifier(bundle_id, label="上传包")
        metadata = _read_json(self._bundle_dir(normalized) / "bundle.json", {})
        if not isinstance(metadata, dict) or metadata.get("id") != normalized:
            raise FileNotFoundError("上传包不存在。")
        return {**metadata, "files": self.list_files(normalized)}

    def list(self) -> list[dict[str, Any]]:
        bundles: list[dict[str, Any]] = []
        for candidate in sorted(self.root.iterdir(), reverse=True):
            if not candidate.is_dir():
                continue
            with contextlib.suppress(FileNotFoundError):
                bundles.append(self.describe(candidate.name))
        return bundles

    def resolve_file(self, bundle_id: str, file_id: str) -> tuple[Path, dict[str, Any]]:
        normalized_file_id = str(file_id or "").strip().lower()
        for entry in self.list_files(bundle_id):
            if secrets.compare_digest(entry["id"], normalized_file_id):
                path = self.files_dir(bundle_id) / Path(*PurePosixPath(entry["relative_path"]).parts)
                return path, entry
        raise FileNotFoundError("上传包文件不存在。")


class RemoteExecutionService:
    TASKS = ("runtime-diagnostics", "cuda-smoke-test", "command")

    def __init__(
        self,
        *,
        paths: AppPaths,
        runtime_provider: Callable[[], dict[str, Any]],
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    ):
        self.paths = paths
        self.runtime_provider = runtime_provider
        self.root = paths.cache_dir / "remote_execution"
        self.jobs_root = self.root / "jobs"
        self.bundles = RemoteBundleStore(
            self.root / "bundles",
            max_upload_bytes=max_upload_bytes,
        )
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="remote-execution")
        self._cancel_events: dict[str, threading.Event] = {}
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._closed = False
        self._mark_interrupted_jobs()

    def _job_dir(self, job_id: str) -> Path:
        return self.jobs_root / _identifier(job_id, label="远程任务")

    def _job_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "job.json"

    def _load_job(self, job_id: str) -> dict[str, Any]:
        normalized = _identifier(job_id, label="远程任务")
        payload = _read_json(self._job_path(normalized), {})
        if not isinstance(payload, dict) or payload.get("id") != normalized:
            raise FileNotFoundError("远程任务不存在。")
        return payload

    def _save_job(self, job: dict[str, Any]) -> None:
        _write_json(self._job_path(str(job["id"])), job)

    def _mark_interrupted_jobs(self) -> None:
        for job_path in self.jobs_root.glob("*/job.json"):
            payload = _read_json(job_path, {})
            if not isinstance(payload, dict) or payload.get("status") not in {"queued", "running"}:
                continue
            payload.update({
                "status": "failed",
                "finished_at": _utc_now(),
                "error": "应用上次退出时任务仍在运行，任务已中断。",
            })
            _write_json(job_path, payload)

    def _artifact_payload(self, job_id: str, path: Path, *, name: str) -> dict[str, Any]:
        stat = path.stat()
        return {
            "id": hashlib.sha256(f"{job_id}\0{name}".encode("utf-8")).hexdigest()[:24],
            "name": name,
            "size_bytes": stat.st_size,
            "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        }

    def list_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        job_dir = self._job_dir(job_id)
        entries: list[dict[str, Any]] = []
        log_path = job_dir / "job.log"
        if log_path.is_file():
            entries.append(self._artifact_payload(job_id, log_path, name="job.log"))
        artifacts_dir = job_dir / "artifacts"
        if artifacts_dir.is_dir():
            for path in sorted(artifacts_dir.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                name = path.relative_to(artifacts_dir).as_posix()
                entries.append(self._artifact_payload(job_id, path, name=name))
        return entries

    def describe_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._load_job(job_id)
        return {
            **job,
            "log_tail": _tail_text(self._job_dir(job_id) / "job.log"),
            "artifacts": self.list_artifacts(job_id),
        }

    def list_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        paths = sorted(
            self.jobs_root.glob("*/job.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in paths[: max(1, min(int(limit), 500))]:
            with contextlib.suppress(FileNotFoundError):
                jobs.append(self.describe_job(path.parent.name))
        return jobs

    def create_job(self, *, task: str, parameters: dict[str, Any]) -> dict[str, Any]:
        normalized_task = str(task or "").strip().lower()
        if normalized_task not in self.TASKS:
            raise ValueError(f"不支持的远程任务：{normalized_task or '(空)'}。")
        if not isinstance(parameters, dict):
            raise ValueError("任务参数必须是对象。")
        with self._lock:
            if self._closed:
                raise RuntimeError("远程任务执行器已经停止。")
            job_id = uuid.uuid4().hex
            job_dir = self.jobs_root / job_id
            (job_dir / "workspace").mkdir(parents=True)
            (job_dir / "artifacts").mkdir(parents=True)
            job = {
                "id": job_id,
                "task": normalized_task,
                "parameters": parameters,
                "status": "queued",
                "created_at": _utc_now(),
                "started_at": "",
                "finished_at": "",
                "exit_code": None,
                "error": "",
            }
            self._save_job(job)
            cancel_event = threading.Event()
            self._cancel_events[job_id] = cancel_event
            self._executor.submit(self._execute_job, job_id, cancel_event)
        return self.describe_job(job_id)

    def _update_job(self, job_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            job = self._load_job(job_id)
            job.update(changes)
            self._save_job(job)
            return job

    def _command_cwd(self, job_id: str, selector: str) -> Path:
        normalized = str(selector or "job").strip()
        if normalized == "job":
            return self._job_dir(job_id) / "workspace"
        if normalized == "code":
            return self.paths.code_dir
        if normalized == "data":
            return self.paths.app_data_dir
        if normalized.startswith("bundle:"):
            return self.bundles.files_dir(normalized.partition(":")[2])
        raise ValueError("cwd 只能是 job、code、data 或 bundle:<id>。")

    def _validated_command(self, parameters: dict[str, Any]) -> tuple[list[str], str, int, dict[str, str], list[str]]:
        raw_argv = parameters.get("argv")
        if not isinstance(raw_argv, list) or not raw_argv or len(raw_argv) > 256:
            raise ValueError("command.argv 必须是包含 1-256 项的数组。")
        argv = [str(value) for value in raw_argv]
        if any(not value or "\x00" in value for value in argv):
            raise ValueError("command.argv 包含空参数或非法字符。")
        timeout_seconds = int(parameters.get("timeout_seconds") or 3600)
        if timeout_seconds < 1 or timeout_seconds > 7 * 24 * 60 * 60:
            raise ValueError("timeout_seconds 必须在 1 秒到 7 天之间。")
        raw_env = parameters.get("env") or {}
        if not isinstance(raw_env, dict) or len(raw_env) > 128:
            raise ValueError("command.env 必须是最多 128 项的对象。")
        env = {str(key): str(value) for key, value in raw_env.items()}
        if any(not key or "\x00" in key or "=" in key or "\x00" in value for key, value in env.items()):
            raise ValueError("command.env 包含非法环境变量。")
        raw_artifacts = parameters.get("artifacts") or []
        if not isinstance(raw_artifacts, list) or len(raw_artifacts) > 256:
            raise ValueError("command.artifacts 必须是最多 256 项的数组。")
        artifacts = [str(value) for value in raw_artifacts]
        return argv, str(parameters.get("cwd") or "job"), timeout_seconds, env, artifacts

    def _run_process(
        self,
        job_id: str,
        *,
        argv: list[str],
        cwd: Path,
        timeout_seconds: int,
        env_overrides: dict[str, str],
        cancel_event: threading.Event,
    ) -> tuple[int | None, str]:
        log_path = self._job_dir(job_id) / "job.log"
        environment = os.environ.copy()
        environment.update(env_overrides)
        started = time.monotonic()
        with log_path.open("ab") as log:
            log.write((f"$ {json.dumps(argv, ensure_ascii=False)}\n").encode("utf-8"))
            log.flush()
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=str(cwd),
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    start_new_session=os.name != "nt",
                    creationflags=(
                        subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
                        if os.name == "nt"
                        else 0
                    ),
                )
            except OSError as exc:
                raise ValueError(f"无法启动命令：{exc}") from exc
            with self._lock:
                self._processes[job_id] = process
            try:
                while process.poll() is None:
                    if cancel_event.wait(0.1):
                        self._terminate_process_tree(process)
                        return process.returncode, "cancelled"
                    if time.monotonic() - started > timeout_seconds:
                        self._terminate_process_tree(process)
                        return process.returncode, "timeout"
                return process.returncode, "finished"
            finally:
                with self._lock:
                    self._processes.pop(job_id, None)

    def _terminate_process_tree(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            with contextlib.suppress(OSError):
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
        else:
            with contextlib.suppress(OSError, ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                with contextlib.suppress(OSError, ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            else:
                with contextlib.suppress(OSError):
                    process.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)

    def _collect_artifacts(self, job_id: str, cwd: Path, patterns: list[str]) -> None:
        if not patterns:
            return
        resolved_cwd = cwd.resolve()
        artifacts_dir = self._job_dir(job_id) / "artifacts"
        for raw_pattern in patterns:
            pattern = str(raw_pattern or "").replace("\\", "/")
            pure = PurePosixPath(pattern)
            if not pattern or pure.is_absolute() or ".." in pure.parts:
                raise ValueError(f"不安全的产物匹配路径：{raw_pattern}")
            for candidate in cwd.glob(pattern):
                if not candidate.is_file() or candidate.is_symlink():
                    continue
                resolved = candidate.resolve(strict=True)
                if resolved_cwd not in resolved.parents:
                    continue
                relative = resolved.relative_to(resolved_cwd)
                target = artifacts_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(resolved, target)

    def _execute_runtime_diagnostics(self, job_id: str) -> int:
        payload = redact_diagnostics_data(self.runtime_provider())
        destination = self._job_dir(job_id) / "artifacts" / "runtime-diagnostics.json"
        _write_json(destination, payload)
        return 0

    def _execute_command(
        self,
        job_id: str,
        parameters: dict[str, Any],
        cancel_event: threading.Event,
    ) -> tuple[int | None, str]:
        argv, cwd_selector, timeout_seconds, env, artifacts = self._validated_command(parameters)
        cwd = self._command_cwd(job_id, cwd_selector)
        if not cwd.is_dir():
            raise ValueError("任务工作目录不存在。")
        exit_code, outcome = self._run_process(
            job_id,
            argv=argv,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            env_overrides=env,
            cancel_event=cancel_event,
        )
        if outcome == "finished":
            self._collect_artifacts(job_id, cwd, artifacts)
        return exit_code, outcome

    def _execute_cuda_smoke_test(
        self,
        job_id: str,
        cancel_event: threading.Event,
    ) -> tuple[int | None, str]:
        script = (
            "import json, platform, torch\n"
            "result={'platform': platform.platform(), 'python': platform.python_version(), "
            "'torch': torch.__version__, 'cuda_available': torch.cuda.is_available(), "
            "'cuda_version': torch.version.cuda, 'devices': []}\n"
            "for i in range(torch.cuda.device_count()):\n"
            " p=torch.cuda.get_device_properties(i); result['devices'].append({"
            "'index': i, 'name': p.name, 'total_memory': p.total_memory, "
            "'capability': list(torch.cuda.get_device_capability(i))})\n"
            "if not torch.cuda.is_available(): raise RuntimeError('PyTorch 未检测到可用 CUDA')\n"
            "x=torch.arange(1024, device='cuda', dtype=torch.float32); "
            "result['sum']=float(x.sum().item())\n"
            "open('cuda-smoke-test.json','w',encoding='utf-8').write(json.dumps(result,ensure_ascii=False,indent=2))\n"
            "print(json.dumps(result,ensure_ascii=False), flush=True)\n"
        )
        return self._execute_command(
            job_id,
            {
                "argv": [sys.executable, "-c", script],
                "cwd": "job",
                "timeout_seconds": 300,
                "artifacts": ["cuda-smoke-test.json"],
            },
            cancel_event,
        )

    def _execute_job(self, job_id: str, cancel_event: threading.Event) -> None:
        if cancel_event.is_set():
            self._update_job(job_id, status="cancelled", finished_at=_utc_now())
            return
        job = self._update_job(job_id, status="running", started_at=_utc_now())
        exit_code: int | None = None
        try:
            if job["task"] == "runtime-diagnostics":
                exit_code = self._execute_runtime_diagnostics(job_id)
                outcome = "finished"
            elif job["task"] == "cuda-smoke-test":
                exit_code, outcome = self._execute_cuda_smoke_test(job_id, cancel_event)
            else:
                exit_code, outcome = self._execute_command(job_id, job["parameters"], cancel_event)
            if outcome == "cancelled" or cancel_event.is_set():
                self._update_job(
                    job_id,
                    status="cancelled",
                    exit_code=exit_code,
                    finished_at=_utc_now(),
                )
            elif outcome == "timeout":
                self._update_job(
                    job_id,
                    status="failed",
                    exit_code=exit_code,
                    error="任务运行超时，进程已停止。",
                    finished_at=_utc_now(),
                )
            elif exit_code == 0:
                self._update_job(
                    job_id,
                    status="completed",
                    exit_code=0,
                    finished_at=_utc_now(),
                )
            else:
                self._update_job(
                    job_id,
                    status="failed",
                    exit_code=exit_code,
                    error=f"命令执行失败，退出码为 {exit_code}。请查看 job.log。",
                    finished_at=_utc_now(),
                )
        except Exception as exc:
            self._update_job(
                job_id,
                status="cancelled" if cancel_event.is_set() else "failed",
                exit_code=exit_code,
                error="" if cancel_event.is_set() else str(exc),
                finished_at=_utc_now(),
            )
        finally:
            with self._lock:
                self._cancel_events.pop(job_id, None)

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        normalized = _identifier(job_id, label="远程任务")
        with self._lock:
            job = self._load_job(normalized)
            if job["status"] in TERMINAL_JOB_STATUSES:
                return self.describe_job(normalized)
            event = self._cancel_events.get(normalized)
            if event is not None:
                event.set()
            process = self._processes.get(normalized)
            if process is not None and process.poll() is None:
                self._terminate_process_tree(process)
            if job["status"] == "queued":
                job.update({"status": "cancelled", "finished_at": _utc_now()})
                self._save_job(job)
        return self.describe_job(normalized)

    def resolve_artifact(self, job_id: str, artifact_id: str) -> tuple[Path, dict[str, Any]]:
        normalized_artifact_id = str(artifact_id or "").strip().lower()
        for entry in self.list_artifacts(job_id):
            if not secrets.compare_digest(entry["id"], normalized_artifact_id):
                continue
            if entry["name"] == "job.log":
                return self._job_dir(job_id) / "job.log", entry
            relative = PurePosixPath(entry["name"])
            return self._job_dir(job_id) / "artifacts" / Path(*relative.parts), entry
        raise FileNotFoundError("任务产物不存在。")

    def status(self) -> dict[str, Any]:
        counts = {status: 0 for status in VALID_JOB_STATUSES}
        for job_path in self.jobs_root.glob("*/job.json"):
            job = _read_json(job_path, {})
            status = str(job.get("status") or "") if isinstance(job, dict) else ""
            if status in counts:
                counts[status] += 1
        return {
            "tasks": list(self.TASKS),
            "job_counts": counts,
            "worker_root": str(self.root),
            "python_executable": sys.executable,
        }

    def cancel_active_jobs(self) -> None:
        with self._lock:
            job_ids = list(self._cancel_events)
        for job_id in job_ids:
            with contextlib.suppress(FileNotFoundError):
                self.cancel_job(job_id)

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for event in self._cancel_events.values():
                event.set()
            for process in self._processes.values():
                if process.poll() is None:
                    self._terminate_process_tree(process)
        self._executor.shutdown(wait=True, cancel_futures=True)


class RemoteJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict)


def _bearer_token(request: Request) -> str:
    scheme, _, candidate = str(request.headers.get("authorization") or "").partition(" ")
    return candidate if scheme.lower() == "bearer" else ""


def create_remote_execution_app(
    *,
    service: RemoteExecutionService,
    access: RemoteExecutionAccess,
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    allowed_hosts: list[str] | None = None,
) -> FastAPI:
    worker_app = FastAPI(
        title="Solar Manga Translator Remote Worker",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    worker_app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=allowed_hosts or ["*"],
    )

    @worker_app.middleware("http")
    async def protect_remote_worker(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            with contextlib.suppress(ValueError):
                if int(content_length) > max_upload_bytes + 1024 * 1024:
                    response = JSONResponse(status_code=413, content={"detail": "上传内容超过允许大小。"})
                    response.headers["Cache-Control"] = "no-store"
                    return response
        if not access.is_authorized(_bearer_token(request)):
            response = JSONResponse(status_code=401, content={"detail": "远程任务令牌无效。"})
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @worker_app.get("/v1/status")
    async def get_status():
        return {
            "service": "solar-manga-translator-remote-worker",
            "capabilities": {
                "tasks": list(service.TASKS),
                "jobs": "/v1/jobs",
                "bundles": "/v1/bundles",
                "max_upload_bytes": max_upload_bytes,
                "command_uses_shell": False,
            },
            "runtime": service.status(),
        }

    @worker_app.get("/v1/jobs")
    async def get_jobs(limit: int = 100):
        return {"jobs": service.list_jobs(limit=limit)}

    @worker_app.post("/v1/jobs", status_code=202)
    async def create_job(payload: RemoteJobRequest):
        try:
            return {"job": service.create_job(task=payload.task, parameters=payload.parameters)}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @worker_app.get("/v1/jobs/{job_id}")
    async def get_job(job_id: str):
        try:
            return {"job": service.describe_job(job_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @worker_app.post("/v1/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        try:
            return {"job": service.cancel_job(job_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @worker_app.get("/v1/jobs/{job_id}/artifacts/{artifact_id}")
    async def get_artifact(job_id: str, artifact_id: str):
        try:
            path, entry = service.resolve_artifact(job_id, artifact_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(
            path,
            media_type=entry["media_type"],
            filename=Path(entry["name"]).name,
            content_disposition_type="attachment",
        )

    @worker_app.get("/v1/bundles")
    async def get_bundles():
        return {"bundles": service.bundles.list()}

    @worker_app.post("/v1/bundles", status_code=201)
    def create_bundle(file: UploadFile = File(...)):
        try:
            return {
                "bundle": service.bundles.create(
                    filename=str(file.filename or "bundle.bin"),
                    source=file.file,
                )
            }
        except (ValueError, zipfile.BadZipFile, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            file.file.close()

    @worker_app.get("/v1/bundles/{bundle_id}")
    async def get_bundle(bundle_id: str):
        try:
            return {"bundle": service.bundles.describe(bundle_id)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @worker_app.get("/v1/bundles/{bundle_id}/files/{file_id}")
    async def get_bundle_file(bundle_id: str, file_id: str):
        try:
            path, entry = service.bundles.resolve_file(bundle_id, file_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(
            path,
            media_type=entry["media_type"],
            filename=Path(entry["relative_path"]).name,
            content_disposition_type="attachment",
        )

    return worker_app


class RemoteExecutionManager:
    def __init__(
        self,
        *,
        paths: AppPaths,
        runtime_provider: Callable[[], dict[str, Any]],
        preferred_port: int = DEFAULT_REMOTE_EXECUTION_PORT,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    ):
        self.paths = paths
        self.preferred_port = preferred_port
        self.max_upload_bytes = max_upload_bytes
        self.config_path = paths.config_dir / "remote_execution.json"
        self.service = RemoteExecutionService(
            paths=paths,
            runtime_provider=runtime_provider,
            max_upload_bytes=max_upload_bytes,
        )
        config = self._load_config()
        self.access = RemoteExecutionAccess(str(config.get("token") or secrets.token_urlsafe(32)))
        self._lock = threading.RLock()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._port = 0

    def _load_config(self) -> dict[str, Any]:
        payload = _read_json(self.config_path, {})
        return payload if isinstance(payload, dict) else {}

    def _save_config(self, *, enabled: bool, token: str) -> None:
        _write_json(self.config_path, {
            "enabled": bool(enabled),
            "token": token,
            "preferred_port": self.preferred_port,
            "updated_at": _utc_now(),
        })

    def _find_available_port(self) -> int:
        for port in range(self.preferred_port, self.preferred_port + 32):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    probe.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
        upper = self.preferred_port + 31
        raise RuntimeError(f"找不到可用的远程任务端口（{self.preferred_port}-{upper}）。")

    def _start_runtime(self, token: str) -> None:
        with self._lock:
            if self._server is not None and self._thread is not None and self._thread.is_alive():
                self.access.set_token(token)
                return
            self.access.set_token(token)
            self._port = self._find_available_port()
            worker_app = create_remote_execution_app(
                service=self.service,
                access=self.access,
                max_upload_bytes=self.max_upload_bytes,
                allowed_hosts=["*"],
            )
            self._server = uvicorn.Server(uvicorn.Config(
                worker_app,
                host="0.0.0.0",
                port=self._port,
                log_level="warning",
                access_log=False,
            ))
            self._thread = threading.Thread(
                target=self._server.run,
                name="remote-execution-server",
                daemon=True,
            )
            self._thread.start()
            deadline = time.monotonic() + 5
            while not self._server.started and self._thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            if not self._server.started:
                self._server.should_exit = True
                self._server = None
                self._thread = None
                self._port = 0
                raise RuntimeError("远程任务节点启动失败，请检查 Windows 防火墙和端口占用。")

    def enable(self, *, rotate_token: bool = False) -> dict[str, Any]:
        config = self._load_config()
        token = str(config.get("token") or "").strip()
        if rotate_token or not token:
            token = secrets.token_urlsafe(32)
        self._save_config(enabled=True, token=token)
        self._start_runtime(token)
        return self.status()

    def rotate_token(self) -> dict[str, Any]:
        return self.enable(rotate_token=True)

    def start_if_enabled(self) -> dict[str, Any]:
        config = self._load_config()
        if not bool(config.get("enabled")):
            return self.status()
        token = str(config.get("token") or "").strip()
        if not token:
            token = secrets.token_urlsafe(32)
            self._save_config(enabled=True, token=token)
        self._start_runtime(token)
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            config = self._load_config()
            enabled = bool(config.get("enabled"))
            active = bool(
                self._server is not None
                and self._thread is not None
                and self._thread.is_alive()
                and self._server.started
            )
            addresses = discover_lan_ipv4_addresses() if active else []
            return {
                "enabled": enabled,
                "active": active,
                "persistent": True,
                "port": self._port if active else 0,
                "urls": [f"http://{address}:{self._port}" for address in addresses],
                "token": str(config.get("token") or "") if enabled else "",
                "tasks": list(self.service.TASKS),
                "worker_root": str(self.service.root),
            }

    def stop_runtime(self) -> dict[str, Any]:
        with self._lock:
            server = self._server
            thread = self._thread
            if server is not None:
                server.should_exit = True
            self._server = None
            self._thread = None
            self._port = 0
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
        return self.status()

    def disable(self) -> dict[str, Any]:
        config = self._load_config()
        self._save_config(enabled=False, token=str(config.get("token") or ""))
        self.service.cancel_active_jobs()
        return self.stop_runtime()

    def shutdown(self) -> None:
        self.stop_runtime()
        self.service.shutdown()
