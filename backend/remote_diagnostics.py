from __future__ import annotations

import hashlib
import ipaddress
import json
import mimetypes
import re
import secrets
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from diagnostics_bundle import redact_diagnostics_data, read_sanitized_log_tail_bytes
from runtime_paths import AppPaths


PROJECT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
ALLOWED_PROJECT_FILE_SUFFIXES = frozenset({".json", ".png", ".jpg", ".jpeg", ".webp"})
MAX_CATALOG_FILES = 5000
DEFAULT_REMOTE_DIAGNOSTICS_PORT = 8765
MIN_REMOTE_DIAGNOSTICS_TTL_SECONDS = 5 * 60
MAX_REMOTE_DIAGNOSTICS_TTL_SECONDS = 2 * 60 * 60


@dataclass(frozen=True)
class DiagnosticFile:
    id: str
    project_id: str
    relative_path: str
    path: Path
    size_bytes: int
    modified_at: str
    media_type: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "relative_path": self.relative_path,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "media_type": self.media_type,
        }


class RemoteDiagnosticsAccess:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._token_digest = b""
        self._expires_at = 0.0
        self._expires_at_iso = ""

    def issue_token(self, *, ttl_seconds: int) -> str:
        normalized_ttl = max(1, int(ttl_seconds))
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._token_digest = hashlib.sha256(token.encode("utf-8")).digest()
            self._expires_at = self._clock() + normalized_ttl
            self._expires_at_iso = (
                datetime.now(timezone.utc) + timedelta(seconds=normalized_ttl)
            ).replace(microsecond=0).isoformat()
        return token

    def revoke(self) -> None:
        with self._lock:
            self._token_digest = b""
            self._expires_at = 0.0
            self._expires_at_iso = ""

    def is_authorized(self, candidate: str) -> bool:
        normalized = str(candidate or "").strip()
        if not normalized:
            return False
        candidate_digest = hashlib.sha256(normalized.encode("utf-8")).digest()
        with self._lock:
            return bool(
                self._token_digest
                and self._clock() < self._expires_at
                and secrets.compare_digest(candidate_digest, self._token_digest)
            )

    def status(self) -> dict[str, Any]:
        with self._lock:
            remaining = max(0, int(self._expires_at - self._clock()))
            return {
                "authorized": bool(self._token_digest and remaining > 0),
                "expires_at": self._expires_at_iso if remaining > 0 else "",
                "remaining_seconds": remaining,
            }


class RemoteDiagnosticsCatalog:
    def __init__(self, paths: AppPaths):
        self.paths = paths

    def _validated_project_id(self, project_id: str) -> str:
        normalized = str(project_id or "")
        if normalized in {".", ".."} or not PROJECT_ID_PATTERN.fullmatch(normalized):
            raise FileNotFoundError("诊断项目不存在。")
        return normalized

    def _safe_project_root(self, base: Path, project_id: str) -> Path:
        normalized_project_id = self._validated_project_id(project_id)
        resolved_base = base.resolve()
        candidate = (resolved_base / normalized_project_id).resolve()
        if resolved_base not in candidate.parents:
            raise FileNotFoundError("诊断项目不存在。")
        return candidate

    def _project_roots(self, project_id: str) -> tuple[tuple[str, Path], ...]:
        return (
            ("project", self._safe_project_root(self.paths.projects_dir, project_id)),
            ("output", self._safe_project_root(self.paths.output_dir, project_id)),
            (
                "cache",
                self._safe_project_root(self.paths.cache_dir / "rerender_cache", project_id),
            ),
            ("logs", self._safe_project_root(self.paths.logs_dir / "tasks", project_id)),
        )

    def list_projects(self) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        if not self.paths.projects_dir.is_dir():
            return projects
        for project_dir in sorted(self.paths.projects_dir.iterdir(), key=lambda path: path.name.lower()):
            if not project_dir.is_dir() or project_dir.is_symlink():
                continue
            try:
                project_id = self._validated_project_id(project_dir.name)
            except FileNotFoundError:
                continue
            manifest_path = project_dir / "project.json"
            manifest: dict[str, Any] = {}
            if manifest_path.is_file() and not manifest_path.is_symlink():
                try:
                    raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest = raw_manifest if isinstance(raw_manifest, dict) else {}
                except (OSError, UnicodeError, json.JSONDecodeError):
                    manifest = {}
            projects.append({
                "project_id": project_id,
                "title": str(manifest.get("title") or manifest.get("project_title") or project_id),
                "updated_at": str(manifest.get("updated_at") or manifest.get("project_updated_at") or ""),
                "workflow_stage": str(manifest.get("workflow_stage") or ""),
                "page_count": int(manifest.get("page_count") or 0),
            })
        return projects

    def list_project_files(self, project_id: str) -> list[DiagnosticFile]:
        normalized_project_id = self._validated_project_id(project_id)
        entries: list[DiagnosticFile] = []
        for scope, root in self._project_roots(normalized_project_id):
            if not root.is_dir():
                continue
            resolved_root = root.resolve()
            for path in sorted(root.rglob("*")):
                if len(entries) >= MAX_CATALOG_FILES:
                    return entries
                if not path.is_file() or path.is_symlink():
                    continue
                if path.suffix.lower() not in ALLOWED_PROJECT_FILE_SUFFIXES:
                    continue
                try:
                    resolved_path = path.resolve(strict=True)
                except OSError:
                    continue
                if resolved_root not in resolved_path.parents:
                    continue
                relative_inside_root = resolved_path.relative_to(resolved_root).as_posix()
                relative_path = f"{scope}/{relative_inside_root}"
                stat = resolved_path.stat()
                file_id = hashlib.sha256(
                    f"{normalized_project_id}\0{relative_path}".encode("utf-8")
                ).hexdigest()[:24]
                entries.append(DiagnosticFile(
                    id=file_id,
                    project_id=normalized_project_id,
                    relative_path=relative_path,
                    path=resolved_path,
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                    .replace(microsecond=0)
                    .isoformat(),
                    media_type=mimetypes.guess_type(resolved_path.name)[0] or "application/octet-stream",
                ))
        return entries

    def resolve_project_file(self, project_id: str, file_id: str) -> DiagnosticFile:
        normalized_file_id = str(file_id or "").strip().lower()
        if len(normalized_file_id) != 24 or any(character not in "0123456789abcdef" for character in normalized_file_id):
            raise FileNotFoundError("诊断文件不存在。")
        for entry in self.list_project_files(project_id):
            if secrets.compare_digest(entry.id, normalized_file_id):
                return entry
        raise FileNotFoundError("诊断文件不存在。")

    def read_json_file(self, entry: DiagnosticFile) -> Any:
        try:
            payload = json.loads(entry.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("诊断 JSON 文件无法读取。") from exc
        return redact_diagnostics_data(payload)

    def read_logs(self, *, max_lines: int) -> list[dict[str, Any]]:
        normalized_max_lines = max(1, min(int(max_lines), 2000))
        logs: list[dict[str, Any]] = []
        if not self.paths.logs_dir.is_dir():
            return logs
        for path in sorted(self.paths.logs_dir.rglob("*.log*")):
            if not path.is_file() or path.is_symlink():
                continue
            try:
                resolved_path = path.resolve(strict=True)
                relative_path = resolved_path.relative_to(self.paths.logs_dir.resolve()).as_posix()
            except (OSError, ValueError):
                continue
            content = read_sanitized_log_tail_bytes(resolved_path).decode("utf-8", errors="replace")
            lines = content.splitlines()[-normalized_max_lines:]
            logs.append({
                "name": relative_path,
                "lines": lines,
            })
        return logs


def _bearer_token(request: Request) -> str:
    scheme, _, candidate = str(request.headers.get("authorization") or "").partition(" ")
    return candidate if scheme.lower() == "bearer" else ""


def create_remote_diagnostics_app(
    *,
    catalog: RemoteDiagnosticsCatalog,
    access: RemoteDiagnosticsAccess,
    runtime_provider: Callable[[], dict[str, Any]],
    allowed_hosts: list[str] | None = None,
) -> FastAPI:
    diagnostic_app = FastAPI(
        title="Solar Manga Translator Read-only Diagnostics",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    diagnostic_app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=allowed_hosts or ["testserver", "localhost", "127.0.0.1"],
    )

    @diagnostic_app.middleware("http")
    async def protect_remote_diagnostics(request: Request, call_next):
        if request.method != "GET":
            response = JSONResponse(status_code=405, content={"detail": "远程诊断服务只允许读取。"})
        elif not access.is_authorized(_bearer_token(request)):
            response = JSONResponse(status_code=401, content={"detail": "远程诊断令牌无效或已过期。"})
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    @diagnostic_app.get("/v1/status")
    async def get_status():
        return {
            "service": "solar-manga-translator-read-only-diagnostics",
            "access": access.status(),
            "capabilities": {
                "projects": "/v1/projects",
                "project_files": "/v1/projects/{project_id}/files",
                "project_file": "/v1/projects/{project_id}/files/{file_id}",
                "logs": "/v1/logs?lines=400",
            },
            "runtime": redact_diagnostics_data(runtime_provider()),
        }

    @diagnostic_app.get("/v1/projects")
    async def get_projects():
        return {"projects": catalog.list_projects()}

    @diagnostic_app.get("/v1/projects/{project_id}/files")
    async def get_project_files(project_id: str):
        try:
            files = catalog.list_project_files(project_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "project_id": project_id,
            "files": [entry.to_payload() for entry in files],
        }

    @diagnostic_app.get("/v1/projects/{project_id}/files/{file_id}")
    async def get_project_file(project_id: str, file_id: str):
        try:
            entry = catalog.resolve_project_file(project_id, file_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if entry.path.suffix.lower() == ".json":
            try:
                return JSONResponse(content=catalog.read_json_file(entry))
            except RuntimeError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        return FileResponse(
            entry.path,
            media_type=entry.media_type,
            filename=entry.path.name,
            content_disposition_type="attachment",
        )

    @diagnostic_app.get("/v1/logs")
    async def get_logs(lines: int = 400):
        return {"logs": catalog.read_logs(max_lines=lines)}

    return diagnostic_app


def discover_lan_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    candidates: list[str] = []
    try:
        candidates.extend(
            item[4][0]
            for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        )
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            candidates.append(str(probe.getsockname()[0]))
    except OSError:
        pass
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version != 4 or address.is_loopback or address.is_link_local or address.is_unspecified:
            continue
        addresses.add(str(address))
    return sorted(addresses)


class RemoteDiagnosticsManager:
    def __init__(
        self,
        *,
        paths: AppPaths,
        runtime_provider: Callable[[], dict[str, Any]],
        preferred_port: int = DEFAULT_REMOTE_DIAGNOSTICS_PORT,
    ):
        self.paths = paths
        self.runtime_provider = runtime_provider
        self.preferred_port = preferred_port
        self.access = RemoteDiagnosticsAccess()
        self.catalog = RemoteDiagnosticsCatalog(paths)
        self._lock = threading.RLock()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._timer: threading.Timer | None = None
        self._port = 0

    def _find_available_port(self) -> int:
        for port in range(self.preferred_port, self.preferred_port + 32):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    probe.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
        raise RuntimeError("找不到可用的局域网诊断端口（8765-8796）。")

    def _allowed_hosts(self, addresses: list[str]) -> list[str]:
        return sorted({
            "127.0.0.1",
            "localhost",
            socket.gethostname(),
            *addresses,
        })

    def start(self, *, ttl_seconds: int = 60 * 60) -> dict[str, Any]:
        normalized_ttl = max(
            MIN_REMOTE_DIAGNOSTICS_TTL_SECONDS,
            min(int(ttl_seconds), MAX_REMOTE_DIAGNOSTICS_TTL_SECONDS),
        )
        with self._lock:
            if self._server is None or self._thread is None or not self._thread.is_alive():
                addresses = discover_lan_ipv4_addresses()
                self._port = self._find_available_port()
                diagnostic_app = create_remote_diagnostics_app(
                    catalog=self.catalog,
                    access=self.access,
                    runtime_provider=self.runtime_provider,
                    allowed_hosts=self._allowed_hosts(addresses),
                )
                config = uvicorn.Config(
                    diagnostic_app,
                    host="0.0.0.0",
                    port=self._port,
                    log_level="warning",
                    access_log=False,
                )
                self._server = uvicorn.Server(config)
                self._thread = threading.Thread(
                    target=self._server.run,
                    name="remote-diagnostics",
                    daemon=True,
                )
                self._thread.start()
                deadline = time.monotonic() + 5.0
                while not self._server.started and self._thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(0.02)
                if not self._server.started:
                    self._server.should_exit = True
                    self._server = None
                    self._thread = None
                    self._port = 0
                    raise RuntimeError("局域网诊断服务启动失败，请检查 Windows 防火墙和端口占用。")

            token = self.access.issue_token(ttl_seconds=normalized_ttl)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(normalized_ttl, self.stop)
            self._timer.daemon = True
            self._timer.start()
            status = self.status()
            status["token"] = token
            return status

    def status(self) -> dict[str, Any]:
        with self._lock:
            active = bool(
                self._server is not None
                and self._thread is not None
                and self._thread.is_alive()
                and self.access.status()["authorized"]
            )
            addresses = discover_lan_ipv4_addresses() if active else []
            return {
                "active": active,
                "read_only": True,
                "port": self._port if active else 0,
                "urls": [f"http://{address}:{self._port}" for address in addresses],
                "expires_at": self.access.status()["expires_at"] if active else "",
                "remaining_seconds": self.access.status()["remaining_seconds"] if active else 0,
            }

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self.access.revoke()
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            server = self._server
            thread = self._thread
            if server is not None:
                server.should_exit = True
            self._server = None
            self._thread = None
            self._port = 0
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        return self.status()
