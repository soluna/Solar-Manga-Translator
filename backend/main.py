from pathlib import Path
from typing import Any
import asyncio
import contextlib
import logging
import os
import secrets
import shutil
import subprocess
import sys
import uuid
from urllib.parse import quote

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from engine.translator import InvalidStorageIdentifierError, TranslatorEngine
from runtime_paths import resolve_app_paths
from system_fonts import BUNDLED_DEFAULT_FONT_NAME
from system_fonts import FONT_EXTENSIONS as SYSTEM_FONT_EXTENSIONS
from system_fonts import bundled_font_directories
from system_fonts import custom_font_directories
from system_fonts import ensure_project_font_directories
from utils.file_handler import extract_archive, verify_supported_image

ENABLE_API_DOCS = os.getenv("APP_ENABLE_API_DOCS", "").strip().lower() in {"1", "true", "yes"}
app = FastAPI(
    title="Solar-Manga-Translator API",
    docs_url="/docs" if ENABLE_API_DOCS else None,
    redoc_url="/redoc" if ENABLE_API_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_API_DOCS else None,
)

BASE_DIR = Path(os.getenv("APP_CODE_DIR") or Path(__file__).resolve().parent).resolve()
APP_PATHS = resolve_app_paths(BASE_DIR)
OUTPUT_DIR = APP_PATHS.output_dir
TEMP_UPLOADS_DIR = APP_PATHS.cache_uploads_dir
TEMP_EXTRACTED_DIR = APP_PATHS.cache_extracted_dir
API_TOKEN = str(os.getenv("APP_API_TOKEN") or os.getenv("MANGA_TRANSLATOR_API_TOKEN") or "").strip()
DEFAULT_MAX_UPLOAD_BYTES = 512 * 1024 * 1024


def positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


MAX_UPLOAD_BYTES = positive_env_int("APP_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES)
MAX_REQUEST_BYTES = max(
    MAX_UPLOAD_BYTES,
    positive_env_int("APP_MAX_REQUEST_BYTES", MAX_UPLOAD_BYTES + 64 * 1024 * 1024),
)
ALLOWED_EXTENSIONS = (".zip", ".cbz", ".jpg", ".jpeg", ".png", ".webp")
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
FONT_EXTENSIONS = tuple(sorted(SYSTEM_FONT_EXTENSIONS))
FONT_ROOT_DIR = ensure_project_font_directories(BASE_DIR)
FONT_DIRECTORIES = {
    "system": (
        *bundled_font_directories(BASE_DIR),
    ),
    "project": (
        *custom_font_directories(BASE_DIR),
    ),
}
LOCAL_LAMA_MODEL_FILENAME = "lama_large_512px.ckpt"
LOCAL_LAMA_MODEL_URL = "https://huggingface.co/dreMaz/AnimeMangaInpainting/resolve/main/lama_large_512px.ckpt"
LOCAL_LAMA_MODEL_MIRROR_URL = "https://hf-mirror.com/dreMaz/AnimeMangaInpainting/resolve/main/lama_large_512px.ckpt"
LOCAL_LAMA_MODEL_SHA256 = "11d30fbb3000fb2eceae318b75d9ced9229d99ae990a7f8b3ac35c8d31f2c935"


def iter_font_directories(source: str) -> list[Path]:
    candidates = FONT_DIRECTORIES.get(source) or ()
    directories: list[Path] = []
    seen: set[str] = set()
    for directory in candidates:
        normalized = str(directory.resolve()) if directory.exists() else str(directory)
        if normalized in seen:
            continue
        seen.add(normalized)
        directories.append(directory)
    return directories


SESSIONS: dict[str, dict[str, Any]] = {}
translator_engine = TranslatorEngine(BASE_DIR, app_paths=APP_PATHS)
logger = logging.getLogger("manga_translator.api")

allowed_hosts = [
    host.strip()
    for host in os.getenv("APP_ALLOWED_HOSTS", "127.0.0.1,localhost,[::1],testserver").split(",")
    if host.strip()
]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# 确保输出和临时目录存在并挂载静态文件
APP_PATHS.ensure_directories()
app.mount("/output", StaticFiles(directory=str(OUTPUT_DIR)), name="output")


class UploadTooLargeError(ValueError):
    pass


def is_public_read_path(path: str, method: str) -> bool:
    if path == "/api/status":
        return True
    if method != "GET":
        return False
    if path.startswith(("/output/", "/api/fonts/file/", "/api/download/")):
        return True
    if path.startswith("/api/pages/"):
        return path.endswith(("/base-image", "/source-image", "/preview-image", "/translated-image"))
    return False


def has_valid_api_token(raw_authorization: str | None) -> bool:
    if not API_TOKEN:
        return True
    scheme, _, candidate = str(raw_authorization or "").partition(" ")
    return scheme.lower() == "bearer" and bool(candidate) and secrets.compare_digest(candidate, API_TOKEN)


def websocket_has_valid_api_token(websocket: WebSocket) -> bool:
    if not API_TOKEN:
        return True
    protocols = {
        protocol.strip()
        for protocol in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if protocol.strip()
    }
    candidate = next(
        (protocol.removeprefix("auth.") for protocol in protocols if protocol.startswith("auth.")),
        "",
    )
    return bool(candidate) and secrets.compare_digest(candidate, API_TOKEN)


@app.middleware("http")
async def protect_local_api(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length:
        with contextlib.suppress(ValueError):
            if int(content_length) > MAX_REQUEST_BYTES:
                return JSONResponse(status_code=413, content={"detail": "上传内容超过允许大小。"})

    if request.method == "OPTIONS":
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    if (
        API_TOKEN
        and not is_public_read_path(request.url.path, request.method)
        and not has_valid_api_token(request.headers.get("authorization"))
    ):
        response = JSONResponse(status_code=401, content={"detail": "需要有效的本地 API 访问令牌。"})
    else:
        response = await call_next(request)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@app.exception_handler(UploadTooLargeError)
async def handle_upload_too_large(_request: Request, exc: UploadTooLargeError):
    return JSONResponse(status_code=413, content={"detail": str(exc)})


@app.exception_handler(InvalidStorageIdentifierError)
async def handle_invalid_storage_identifier(_request: Request, exc: InvalidStorageIdentifierError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def copy_upload_to_path(upload: UploadFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    copied = 0
    try:
        with destination.open("wb") as buffer:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > MAX_UPLOAD_BYTES:
                    raise UploadTooLargeError("单个上传文件超过允许大小。")
                buffer.write(chunk)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            destination.unlink()
        raise


def configure_app_logging() -> None:
    log_path = APP_PATHS.backend_log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    normalized_log_path = str(log_path.resolve())
    has_file_handler = any(
        isinstance(handler, logging.FileHandler)
        and str(Path(getattr(handler, "baseFilename", "")).resolve()) == normalized_log_path
        for handler in root_logger.handlers
    )
    has_stream_handler = any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
        for handler in root_logger.handlers
    )
    if not has_file_handler:
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root_logger.addHandler(file_handler)

    if not has_stream_handler:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root_logger.addHandler(stream_handler)


configure_app_logging()
logger.info("Backend API initialized. data_dir=%s output_dir=%s logs_dir=%s", APP_PATHS.app_data_dir, APP_PATHS.output_dir, APP_PATHS.logs_dir)


def prepare_session_images(session_id: str, image_paths: list[str]) -> tuple[Path, list[dict[str, str]]]:
    source_dir = OUTPUT_DIR / session_id / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    prepared_images: list[dict[str, str]] = []
    for index, image_path in enumerate(image_paths, start=1):
        source_path = Path(image_path)
        suffix = source_path.suffix.lower() or ".png"
        stored_name = f"{index:04d}{suffix}"
        target_path = source_dir / stored_name
        shutil.copy2(source_path, target_path)

        prepared_images.append(
            {
                "name": source_path.name,
                "stored_name": stored_name,
                "url": f"/output/{session_id}/source/{stored_name}",
            }
        )

    return source_dir, prepared_images


def list_available_fonts() -> list[dict[str, str]]:
    fonts: list[dict[str, str]] = []
    preferred_order = {
        BUNDLED_DEFAULT_FONT_NAME: 0,
        "SourceHanSansSC-Medium-2.otf": 1,
        "SourceHanSansSC-Bold.otf": 2,
    }

    for source in FONT_DIRECTORIES:
        seen_ids: set[str] = set()
        for font_dir in iter_font_directories(source):
            if not font_dir.exists():
                continue

            try:
                font_paths = sorted(
                    (
                        path for path in font_dir.rglob("*")
                        if path.is_file() and path.suffix.lower() in FONT_EXTENSIONS
                    ),
                    key=lambda path: (preferred_order.get(path.name, 99), path.name.lower()),
                )
            except OSError:
                continue

            for path in font_paths:
                relative_name = path.relative_to(font_dir).as_posix()
                font_id = f"{source}:{relative_name}"
                if font_id in seen_ids:
                    continue
                seen_ids.add(font_id)
                source_label = "预置" if source == "system" else "自定义"
                suffix = path.suffix.lower()
                format_hint = {
                    ".ttf": "truetype",
                    ".otf": "opentype",
                }.get(suffix, "")
                fonts.append(
                    {
                        "id": font_id,
                        "name": path.name,
                        "label": f"{path.stem} ({source_label})",
                        "source": source,
                        "extension": suffix,
                        "format_hint": format_hint,
                        "url": f"/api/fonts/file/{source}/{quote(relative_name, safe='/')}",
                    }
                )

    return fonts


def resolve_font_file(source: str, font_name: str) -> Path:
    normalized_name = str(font_name or "").replace("\\", "/").strip("/")
    if not normalized_name or any(part in {"", ".", ".."} for part in normalized_name.split("/")):
        raise HTTPException(status_code=404, detail="字体文件不存在。")
    for font_dir in iter_font_directories(source):
        if not font_dir.exists():
            continue
        target_path = (font_dir / Path(*normalized_name.split("/"))).resolve()
        resolved_root = font_dir.resolve()
        if (
            target_path.is_file()
            and target_path.suffix.lower() in FONT_EXTENSIONS
            and resolved_root in target_path.parents
        ):
            return target_path
    raise HTTPException(status_code=404, detail="字体文件不存在。")


def get_or_restore_session(project_id: str) -> dict[str, Any]:
    session = SESSIONS.get(project_id)
    if session:
        return session

    try:
        session = translator_engine.restore_project_session(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    SESSIONS[project_id] = session
    return session


def preserve_single_upload_name(temp_path: Path, target_dir: Path, filename: str) -> str:
    target_path = target_dir / filename
    shutil.copy2(temp_path, target_path)
    if not verify_supported_image(target_path):
        raise ValueError("上传的文件不是有效的受支持图片。")
    return str(target_path)


def is_supported_image_filename(filename: str) -> bool:
    return filename.lower().endswith(IMAGE_EXTENSIONS)


def sanitize_upload_relative_path(raw_path: str, fallback_name: str) -> Path:
    normalized = str(raw_path or "").replace("\\", "/").strip()
    fallback = Path(fallback_name or "image").name
    parts = []
    for part in normalized.split("/"):
        safe_part = Path(part).name.strip()
        if not safe_part or safe_part in {".", ".."}:
            continue
        parts.append(safe_part)
    if not parts:
        parts = [fallback]
    return Path(*parts)


def unique_child_path(root: Path, relative_path: Path) -> Path:
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix
    for index in range(2, 10000):
        candidate = target.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"文件名冲突过多，无法保存: {target.name}")


async def preserve_uploaded_image_files(
    uploads: list[UploadFile],
    relative_paths: list[str],
    target_dir: Path,
) -> list[str]:
    target_dir.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[str, Path, UploadFile]] = []
    for index, upload in enumerate(uploads):
        filename = Path(upload.filename or f"image-{index + 1}").name
        raw_relative_path = relative_paths[index] if index < len(relative_paths) else (upload.filename or filename)
        relative_path = sanitize_upload_relative_path(raw_relative_path, filename)
        if not is_supported_image_filename(str(relative_path)):
            continue
        staged.append((str(relative_path).replace("\\", "/").lower(), relative_path, upload))

    images: list[str] = []
    for _sort_key, relative_path, upload in sorted(staged, key=lambda item: item[0]):
        destination = unique_child_path(target_dir, relative_path)
        await asyncio.to_thread(copy_upload_to_path, upload, destination)
        if await asyncio.to_thread(verify_supported_image, destination):
            images.append(str(destination))
    return images


def build_runtime_payload(request: Request | None = None) -> dict[str, Any]:
    base_url = ""
    if request is not None:
        base_url = str(request.base_url).rstrip("/")
    migration = APP_PATHS.legacy_status()
    return {
        "desktop_mode": os.getenv("APP_DESKTOP_MODE") == "1",
        "app_version": os.getenv("APP_VERSION") or "dev",
        "backend_base_url": base_url,
        "data_dir": str(APP_PATHS.app_data_dir),
        "models_dir": str(APP_PATHS.models_dir),
        "output_dir": str(APP_PATHS.output_dir),
        "logs_dir": str(APP_PATHS.logs_dir),
        "settings_path": str(APP_PATHS.settings_path),
        "settings_exists": APP_PATHS.settings_path.exists(),
        "font_root": str(FONT_ROOT_DIR),
        "font_dirs": {
            source: [str(path) for path in iter_font_directories(source)]
            for source in FONT_DIRECTORIES
        },
        "migration": migration,
    }


def build_runtime_diagnostics() -> dict[str, Any]:
    disk_total, disk_used, disk_free = shutil.disk_usage(APP_PATHS.app_data_dir)
    gpu = {
        "available": False,
        "device_count": 0,
        "devices": [],
        "cuda_version": "",
    }
    try:
        import torch  # type: ignore

        gpu["available"] = bool(torch.cuda.is_available())
        gpu["device_count"] = int(torch.cuda.device_count()) if gpu["available"] else 0
        gpu["cuda_version"] = str(getattr(torch.version, "cuda", "") or "")
        if gpu["available"]:
            gpu["devices"] = [
                {
                    "index": index,
                    "name": str(torch.cuda.get_device_name(index)),
                }
                for index in range(gpu["device_count"])
            ]
    except Exception as exc:
        gpu["error"] = str(exc)

    return {
        "platform": sys.platform,
        "python_version": sys.version.split()[0],
        "disk": {
            "total_bytes": disk_total,
            "used_bytes": disk_used,
            "free_bytes": disk_free,
        },
        "gpu": gpu,
        "paths": {
            "data_dir": str(APP_PATHS.app_data_dir),
            "models_dir": str(APP_PATHS.models_dir),
            "output_dir": str(APP_PATHS.output_dir),
            "logs_dir": str(APP_PATHS.logs_dir),
        },
    }


def build_local_lama_model_payload() -> dict[str, Any]:
    model_path = APP_PATHS.models_dir / "inpainting" / LOCAL_LAMA_MODEL_FILENAME
    partial_path = model_path.with_suffix(f"{model_path.suffix}.part")
    model_size = model_path.stat().st_size if model_path.exists() else 0
    partial_size = partial_path.stat().st_size if partial_path.exists() else 0
    return {
        "model": "lama_large",
        "filename": LOCAL_LAMA_MODEL_FILENAME,
        "download_url": LOCAL_LAMA_MODEL_URL,
        "mirror_url": LOCAL_LAMA_MODEL_MIRROR_URL,
        "sha256": LOCAL_LAMA_MODEL_SHA256,
        "models_dir": str(APP_PATHS.models_dir),
        "model_dir": str(model_path.parent),
        "path": str(model_path),
        "partial_path": str(partial_path),
        "downloaded": model_path.exists(),
        "size_bytes": model_size,
        "partial_downloaded": partial_path.exists(),
        "partial_size_bytes": partial_size,
    }


def read_log_tail(path: Path, max_lines: int = 200) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-max(1, min(max_lines, 2000)):]


def open_directory_in_file_manager(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        return str(exc)
    return ""


@app.get("/api/status")
async def get_status():
    return {"status": "running", "auth_required": bool(API_TOKEN)}


@app.get("/api/app/runtime")
async def get_app_runtime(request: Request):
    return {"runtime": build_runtime_payload(request)}


@app.get("/api/app/diagnostics")
async def get_app_diagnostics():
    return {"diagnostics": build_runtime_diagnostics()}


@app.get("/api/app/local-models/lama-large")
async def get_local_lama_model_status():
    return {"model": build_local_lama_model_payload()}


@app.get("/api/app/settings")
async def get_app_settings():
    settings = translator_engine.load_persisted_settings()
    return {"settings": settings}


@app.patch("/api/app/settings")
async def patch_app_settings(payload: dict[str, Any] | None = None):
    settings = translator_engine.save_persisted_settings(payload or {})
    return {"settings": settings}


@app.post("/api/app/settings/validate")
async def validate_app_settings(payload: dict[str, Any] | None = None):
    return await translator_engine.validate_user_config(payload or {})


@app.get("/api/app/migration-status")
async def get_app_migration_status():
    return {"migration": APP_PATHS.legacy_status()}


@app.post("/api/app/migrate-legacy")
async def migrate_legacy_data(payload: dict[str, Any] | None = None):
    action = str((payload or {}).get("action") or "skip").strip().lower()
    try:
        migration = APP_PATHS.migrate_legacy(action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"migration": migration}


@app.get("/api/app/logs/tail")
async def get_app_logs_tail(lines: int = 200):
    return {
        "path": str(APP_PATHS.backend_log_path),
        "lines": read_log_tail(APP_PATHS.backend_log_path, max_lines=lines),
    }


@app.post("/api/app/open-user-fonts")
async def open_user_fonts_directory():
    error = open_directory_in_file_manager(FONT_ROOT_DIR)
    return {
        "ok": not error,
        "path": str(FONT_ROOT_DIR),
        "error": error,
    }


@app.get("/api/fonts")
async def get_fonts():
    return {"fonts": list_available_fonts()}


@app.get("/api/fonts/file/{source}/{font_name:path}")
async def get_font_file(source: str, font_name: str):
    font_path = resolve_font_file(source, font_name)
    media_type = {
        ".ttf": "font/ttf",
        ".otf": "font/otf",
        ".ttc": "font/collection",
    }.get(font_path.suffix.lower())
    return FileResponse(font_path, media_type=media_type)


@app.get("/api/projects")
async def list_projects():
    return {"projects": translator_engine.list_projects()}


@app.patch("/api/projects/{project_id}")
async def update_project(project_id: str, payload: dict[str, Any] | None = None):
    session = get_or_restore_session(project_id)

    payload = payload or {}
    summary = translator_engine.update_project_metadata(
        project_id=project_id,
        session=session,
        title=payload.get("title"),
        note=payload.get("note"),
        review_mode=payload.get("review_mode"),
    )
    return {"project": summary}


@app.post("/api/projects/{project_id}/restore")
async def restore_project(project_id: str):
    try:
        session = translator_engine.restore_project_session(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    SESSIONS[project_id] = session
    return translator_engine.build_client_session_payload(project_id, session)


@app.post("/api/projects/{project_id}/base-images")
async def upload_project_base_images(project_id: str, file: UploadFile = File(...)):
    session = get_or_restore_session(project_id)
    if translator_engine.is_session_busy(project_id):
        raise HTTPException(status_code=409, detail="该项目当前有任务在运行，请稍后再补充无字图。")

    filename = Path(file.filename or "upload").name
    if not filename.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(status_code=400, detail="Unsupported file format")

    upload_token = str(uuid.uuid4())
    file_path = TEMP_UPLOADS_DIR / f"{upload_token}_{filename}"
    extract_dir = TEMP_EXTRACTED_DIR / f"{project_id}_base_{upload_token}"
    extract_dir.mkdir(exist_ok=True)

    try:
        await asyncio.to_thread(copy_upload_to_path, file, file_path)
        if filename.lower().endswith((".zip", ".cbz")):
            images = await asyncio.to_thread(extract_archive, str(file_path), str(extract_dir))
        else:
            images = [preserve_single_upload_name(file_path, extract_dir, filename)]
        if not images:
            raise ValueError("文件中没有找到有效的受支持图片。")
        result = translator_engine.attach_base_images(project_id, session, images)
    except UploadTooLargeError:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to attach base images. project_id=%s", project_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        with contextlib.suppress(FileNotFoundError):
            file_path.unlink()
        shutil.rmtree(extract_dir, ignore_errors=True)

    return {
        **translator_engine.build_client_session_payload(project_id, session),
        "base_image_upload": result,
    }


@app.get("/api/projects/{project_id}/glossary")
async def get_project_glossary(project_id: str, include_occurrences: bool = False):
    session = get_or_restore_session(project_id)
    return {
        "glossary": translator_engine.get_project_glossary(
            project_id,
            session,
            include_occurrences=include_occurrences,
        )
    }


@app.put("/api/projects/{project_id}/glossary")
async def save_project_glossary(project_id: str, payload: dict[str, Any] | None = None):
    session = get_or_restore_session(project_id)
    payload = payload or {}
    entries = payload.get("entries") or []
    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="名词库条目格式不正确。")
    glossary = translator_engine.save_project_glossary(project_id, session, entries)
    return {"glossary": glossary}


@app.post("/api/projects/{project_id}/glossary/extract")
async def extract_project_glossary(project_id: str, payload: dict[str, Any] | None = None):
    session = get_or_restore_session(project_id)
    if translator_engine.is_session_busy(project_id):
        raise HTTPException(status_code=409, detail="该项目当前有任务在运行，请稍后再提取专有名词。")

    config = translator_engine.capture_page_command_config(session, (payload or {}).get("config") or session.get("last_config") or {})
    glossary = await translator_engine.extract_project_glossary(project_id, session, config, force=True)
    return {"glossary": glossary}


@app.post("/api/projects/{project_id}/glossary/preview")
async def preview_project_glossary_application(project_id: str, payload: dict[str, Any] | None = None):
    session = get_or_restore_session(project_id)
    payload = payload or {}
    entries = payload.get("entries")
    if entries is not None and not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="名词库条目格式不正确。")
    return translator_engine.preview_project_glossary_application(project_id, session, entries)


@app.post("/api/projects/{project_id}/glossary/apply")
async def apply_project_glossary(project_id: str, payload: dict[str, Any] | None = None):
    session = get_or_restore_session(project_id)
    payload = payload or {}
    entries = payload.get("entries") or []
    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="名词库条目格式不正确。")
    if not translator_engine.try_mark_session_busy(project_id, "glossary"):
        raise HTTPException(status_code=409, detail="该项目当前有任务在运行，请稍后再应用专有名词。")
    try:
        return await translator_engine.apply_project_glossary(project_id, session, entries)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        translator_engine.clear_session_busy(project_id)


@app.get("/api/projects/{project_id}/snapshots")
async def list_project_snapshots(project_id: str):
    return {"snapshots": translator_engine.list_project_snapshots(project_id)}


@app.post("/api/projects/{project_id}/snapshots/{snapshot_id}/restore")
async def restore_project_snapshot(project_id: str, snapshot_id: str):
    try:
        restored_project_id, session = translator_engine.restore_snapshot_as_project(project_id, snapshot_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    SESSIONS[restored_project_id] = session
    return translator_engine.build_client_session_payload(restored_project_id, session)


@app.post("/api/projects/{project_id}/snapshots/{snapshot_id}/pin")
async def pin_project_snapshot(project_id: str, snapshot_id: str, payload: dict[str, Any] | None = None):
    payload = payload or {}
    try:
        snapshots = translator_engine.set_snapshot_pinned(
            project_id=project_id,
            snapshot_id=snapshot_id,
            pinned=bool(payload.get("pinned", True)),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"snapshots": snapshots}


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    try:
        translator_engine.delete_project(project_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    SESSIONS.pop(project_id, None)
    return {"ok": True}


@app.post("/api/style-regions/{session_id}")
async def inspect_style_regions(session_id: str, payload: dict[str, Any] | None = None):
    session = get_or_restore_session(session_id)
    payload = payload or {}

    return await translator_engine.inspect_style_regions(
        session_id=session_id,
        session=session,
        raw_config=payload.get("config", {}),
        target_stored_name=str(payload.get("target_stored_name") or "").strip() or None,
    )


@app.post("/api/review-regions/{session_id}")
async def inspect_review_regions(session_id: str, payload: dict[str, Any] | None = None):
    session = get_or_restore_session(session_id)
    payload = payload or {}

    return await translator_engine.inspect_translation_regions(
        session_id=session_id,
        session=session,
        raw_config=payload.get("config", {}),
        target_stored_name=str(payload.get("target_stored_name") or "").strip() or None,
    )


@app.post("/api/manual-regions/{session_id}")
async def update_manual_regions(session_id: str, payload: dict[str, Any] | None = None):
    session = get_or_restore_session(session_id)

    payload = payload or {}
    action = str(payload.get("action") or "create").strip().lower()

    try:
        if action == "delete":
            region_id = str(payload.get("region_id") or "").strip()
            if not region_id:
                raise HTTPException(status_code=400, detail="缺少需要删除的补漏框 ID。")
            removed = translator_engine.delete_manual_region(session, region_id)
            if not removed:
                raise HTTPException(status_code=404, detail="没有找到对应的补漏框。")
            translator_engine.persist_project_state(
                session_id,
                session,
                snapshot_kind="manual_region_deleted",
                snapshot_summary="删除手动补漏框",
                persist_page_documents=True,
            )
            return {"ok": True, "action": "delete", "region_id": region_id}

        if action == "merge":
            translator_engine.capture_session_config(session, payload.get("config", {}))
            stored_name = str(payload.get("stored_name") or "").strip()
            region_ids = payload.get("region_ids") or []
            if not stored_name:
                raise HTTPException(status_code=400, detail="缺少目标页面信息。")
            if not isinstance(region_ids, list) or len(region_ids) < 2:
                raise HTTPException(status_code=400, detail="至少需要选择两个文本框才能合并。")
            region = await translator_engine.merge_regions(
                session_id=session_id,
                session=session,
                raw_config=payload.get("config", {}),
                stored_name=stored_name,
                region_ids=region_ids,
            )
            translator_engine.persist_project_state(
                session_id,
                session,
                snapshot_kind="regions_merged",
                snapshot_summary=f"{stored_name} 合并文本框",
                persist_page_documents=True,
            )
            return {"ok": True, "action": "merge", "region": region}

        translator_engine.capture_session_config(session, payload.get("config", {}))
        stored_name = str(payload.get("stored_name") or "").strip()
        if not stored_name:
            raise HTTPException(status_code=400, detail="缺少目标页面信息。")

        region = await translator_engine.create_manual_region(
            session_id=session_id,
            session=session,
            raw_config=payload.get("config", {}),
            stored_name=stored_name,
            bbox=payload.get("bbox"),
        )
        translator_engine.persist_project_state(
            session_id,
            session,
            snapshot_kind="manual_region_added",
            snapshot_summary=f"{stored_name} 新增手动补漏框",
            persist_page_documents=True,
        )
        return {"ok": True, "action": "create", "region": region}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/pages/{session_id}/{page_id}/document")
async def get_page_document(session_id: str, page_id: str):
    session = get_or_restore_session(session_id)
    try:
        document = translator_engine.get_page_document(session_id, session, page_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"document": document}


@app.get("/api/pages/{session_id}/{page_id}/base-image")
async def get_page_base_image(session_id: str, page_id: str, max_side: int | None = None):
    session = get_or_restore_session(session_id)
    try:
        response_path = await asyncio.to_thread(
            lambda: translator_engine.get_page_image_response_path(
                translator_engine.get_page_base_image_path(session_id, session, page_id),
                session_id,
                page_id,
                "base",
                max_side,
            )
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path=response_path)


@app.get("/api/pages/{session_id}/{page_id}/source-image")
async def get_page_source_image(session_id: str, page_id: str, max_side: int | None = None):
    session = get_or_restore_session(session_id)
    try:
        response_path = await asyncio.to_thread(
            lambda: translator_engine.get_page_image_response_path(
                translator_engine.get_page_source_image_path(session_id, session, page_id),
                session_id,
                page_id,
                "source",
                max_side,
            )
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path=response_path)


@app.get("/api/pages/{session_id}/{page_id}/preview-image")
async def get_page_preview_image(session_id: str, page_id: str, max_side: int | None = None):
    session = get_or_restore_session(session_id)
    try:
        response_path = await asyncio.to_thread(
            lambda: translator_engine.get_page_image_response_path(
                translator_engine.get_page_preview_image_path(session_id, session, page_id),
                session_id,
                page_id,
                "preview",
                max_side,
            )
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path=response_path)


@app.get("/api/pages/{session_id}/{page_id}/translated-image")
async def get_page_translated_image(session_id: str, page_id: str, max_side: int | None = None):
    session = get_or_restore_session(session_id)
    try:
        response_path = await asyncio.to_thread(
            lambda: translator_engine.get_page_image_response_path(
                translator_engine.get_page_translated_image_path(session_id, session, page_id),
                session_id,
                page_id,
                "translated",
                max_side,
            )
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path=response_path)


@app.get("/api/pages/{session_id}/{page_id}/ocr-debug")
async def get_page_ocr_debug(session_id: str, page_id: str):
    session = get_or_restore_session(session_id)
    try:
        payload = translator_engine.get_page_ocr_debug(session_id, session, page_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return payload


@app.get("/api/pages/{session_id}/{page_id}/translation-input-debug")
async def get_page_translation_input_debug(session_id: str, page_id: str):
    session = get_or_restore_session(session_id)
    try:
        payload = translator_engine.get_page_translation_input_debug(session_id, session, page_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return payload


@app.get("/api/projects/{project_id}/translation-request-debug")
async def get_project_translation_request_debug(project_id: str):
    _ = get_or_restore_session(project_id)
    try:
        payload = translator_engine.get_translation_request_debug(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return payload


@app.post("/api/pages/{session_id}/{page_id}/commands")
async def apply_page_commands(session_id: str, page_id: str, payload: dict[str, Any] | None = None):
    session = get_or_restore_session(session_id)
    payload = payload or {}

    try:
        result = await translator_engine.apply_page_commands(
            project_id=session_id,
            session=session,
            page_id=page_id,
            raw_config=payload.get("config", {}),
            commands=payload.get("commands") or [],
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return result


@app.post("/api/pages/{session_id}/{page_id}/advanced-erase")
async def advanced_erase_page(session_id: str, page_id: str, payload: dict[str, Any] | None = None):
    session = get_or_restore_session(session_id)
    payload = payload or {}
    action = str(payload.get("action") or "erase").strip().lower() or "erase"

    if not translator_engine.try_mark_session_busy(session_id, "advanced-erase"):
        raise HTTPException(status_code=409, detail="该项目已有任务在运行，请等待当前任务完成。")

    try:
        result = await translator_engine.advanced_erase_page(
            project_id=session_id,
            session=session,
            page_id=page_id,
            raw_config=payload.get("config", {}),
            action=action,
            selections=payload.get("selections"),
            local_mask_mode=payload.get("local_mask_mode") or payload.get("mask_mode"),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        translator_engine.clear_session_busy(session_id)

    return result


@app.post("/api/pages/{session_id}/{page_id}/brush-edit")
async def brush_edit_page(session_id: str, page_id: str, payload: dict[str, Any] | None = None):
    session = get_or_restore_session(session_id)
    payload = payload or {}

    if not translator_engine.try_mark_session_busy(session_id, "brush-edit"):
        raise HTTPException(status_code=409, detail="该项目已有任务在运行，请等待当前任务完成。")

    try:
        result = await asyncio.to_thread(
            translator_engine.brush_edit_page,
            session_id,
            session,
            page_id,
            payload.get("operations"),
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        translator_engine.clear_session_busy(session_id)

    return result


@app.post("/api/upload")
async def upload_comic(
    file: UploadFile | None = File(None),
    files: list[UploadFile] = File(default_factory=list),
    relative_paths: list[str] = Form(default_factory=list),
    folder_name: str | None = Form(None),
    review_mode: str = Form(TranslatorEngine.DEFAULT_REVIEW_MODE),
):
    session_id = str(uuid.uuid4())
    extract_dir = TEMP_EXTRACTED_DIR / session_id
    extract_dir.mkdir(exist_ok=True)
    file_path: Path | None = None

    try:
        folder_uploads = [upload for upload in (files or []) if upload is not None]
        if folder_uploads:
            images = await preserve_uploaded_image_files(
                folder_uploads,
                relative_paths,
                extract_dir,
            )
            if not images:
                raise ValueError("文件夹中没有找到有效的受支持图片。")
            project_title = Path(str(folder_name or "").strip()).name or "图片文件夹"
        else:
            if file is None:
                raise HTTPException(status_code=400, detail="请先选择 zip/cbz、单张图片或图片文件夹。")
            filename = Path(file.filename or "upload").name
            if not filename.lower().endswith(ALLOWED_EXTENSIONS):
                raise HTTPException(status_code=400, detail="Unsupported file format")

            file_path = TEMP_UPLOADS_DIR / f"{session_id}_{filename}"
            await asyncio.to_thread(copy_upload_to_path, file, file_path)

            if filename.lower().endswith((".zip", ".cbz")):
                images = await asyncio.to_thread(extract_archive, str(file_path), str(extract_dir))
            else:
                images = [preserve_single_upload_name(file_path, extract_dir, filename)]
            if not images:
                raise ValueError("文件中没有找到有效的受支持图片。")
            project_title = Path(filename).stem

        source_dir, staged_images = prepare_session_images(session_id, images)
        translated_dir = OUTPUT_DIR / session_id / "translated"
        translated_dir.mkdir(parents=True, exist_ok=True)

        SESSIONS[session_id] = {
            "source_dir": str(source_dir),
            "translated_dir": str(translated_dir),
            "source_images": staged_images,
            "download_path": None,
            "translated_output_map": {},
            "rerender_generation": 0,
            "manual_regions": {},
            "workflow_stage": "idle",
            "review_mode": review_mode,
        }
        translator_engine.initialize_project(
            session_id,
            SESSIONS[session_id],
            title=project_title,
            review_mode=review_mode,
        )
    except UploadTooLargeError:
        raise
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        logger.exception("Failed to create project from upload. session_id=%s", session_id)
        SESSIONS.pop(session_id, None)
        shutil.rmtree(OUTPUT_DIR / session_id, ignore_errors=True)
        raise
    finally:
        if file_path is not None:
            with contextlib.suppress(FileNotFoundError):
                file_path.unlink()
        shutil.rmtree(extract_dir, ignore_errors=True)

    return translator_engine.build_client_session_payload(session_id, SESSIONS[session_id])


@app.get("/api/download/{session_id}")
async def download_translated_archive(session_id: str):
    session = get_or_restore_session(session_id)

    download_path = Path(
        translator_engine.build_session_archive(
            session_id=session_id,
            session=session,
        )
    )
    if not download_path.exists():
        raise HTTPException(status_code=404, detail="Translated archive not found")

    return FileResponse(
        path=download_path,
        media_type="application/zip",
        filename=translator_engine.get_export_archive_filename(session_id, session, "result"),
    )


@app.get("/api/download/{session_id}/blank")
async def download_blank_archive(session_id: str):
    session = get_or_restore_session(session_id)
    download_path = Path(
        translator_engine.build_blank_session_archive(
            session_id=session_id,
            session=session,
        )
    )
    if not download_path.exists():
        raise HTTPException(status_code=404, detail="Blank archive not found")

    return FileResponse(
        path=download_path,
        media_type="application/zip",
        filename=translator_engine.get_export_archive_filename(session_id, session, "blank"),
    )


@app.websocket("/ws/translate/{session_id}")
async def translate_session(websocket: WebSocket, session_id: str):
    if not websocket_has_valid_api_token(websocket):
        logger.warning("Translation websocket rejected because the local API token was invalid.")
        await websocket.close(code=1008)
        return

    offered_protocols = {
        protocol.strip()
        for protocol in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if protocol.strip()
    }
    selected_protocol = "manga-translator" if "manga-translator" in offered_protocols else None
    await websocket.accept(subprotocol=selected_protocol)
    busy_cleared = False
    action = "translate"
    target_stored_name = ""

    try:
        session = get_or_restore_session(session_id)
    except HTTPException:
        logger.warning("Translation websocket rejected because session was not found. session_id=%s", session_id)
        await websocket.send_json({"event": "error", "message": "会话不存在，请重新上传文件。"})
        await websocket.close()
        return

    try:
        payload = await websocket.receive_json()
        action = str(payload.get("action") or "translate").strip().lower()
        config = payload.get("config", {})
        target_stored_name = str(payload.get("target_stored_name") or "").strip()
        logger.info(
            "Translation websocket started. session_id=%s action=%s target=%s",
            session_id,
            action,
            target_stored_name or "*",
        )

        if not translator_engine.try_mark_session_busy(session_id, action):
            logger.warning("Translation websocket rejected because session is busy. session_id=%s action=%s", session_id, action)
            await websocket.send_json({"event": "error", "message": "该项目已有任务在运行，请等待当前任务完成。"})
            await websocket.close()
            return

        async def send_event(event: dict[str, Any]) -> None:
            try:
                await websocket.send_json(event)
            except Exception:
                logger.exception(
                    "Failed to send translation progress event. session_id=%s action=%s event=%s",
                    session_id,
                    action,
                    event.get("event") if isinstance(event, dict) else type(event).__name__,
                )
                raise

        if action == "rerender":
            result = await translator_engine.rerender_session(
                session_id=session_id,
                session=session,
                raw_config=config,
                progress_callback=send_event,
                target_stored_name=target_stored_name or None,
            )
        elif action == "detect":
            result = await translator_engine.detect_session(
                session_id=session_id,
                session=session,
                raw_config=config,
                progress_callback=send_event,
            )
        elif action == "resume-translate":
            result = await translator_engine.resume_translation_session(
                session_id=session_id,
                session=session,
                raw_config=config,
                progress_callback=send_event,
                skip_completed=True,
            )
        elif action == "translate-page":
            result = await translator_engine.resume_translation_session(
                session_id=session_id,
                session=session,
                raw_config=config,
                progress_callback=send_event,
                target_stored_name=target_stored_name or None,
            )
        else:
            result = await translator_engine.translate_session(
                session_id=session_id,
                session=session,
                raw_config=config,
                progress_callback=send_event,
            )
        logger.info("Translation websocket completed. session_id=%s action=%s", session_id, action)
        completed_payload = {
            "event": "completed",
            **translator_engine.build_client_session_payload(session_id, session),
            **result,
        }
        if isinstance(completed_payload.get("project"), dict):
            completed_payload["project"] = {
                **completed_payload["project"],
                "is_busy": False,
                "busy_action": "",
            }
        await websocket.send_json(completed_payload)
        translator_engine.clear_session_busy(session_id)
        busy_cleared = True
    except WebSocketDisconnect:
        logger.warning(
            "Translation websocket disconnected. session_id=%s action=%s target=%s",
            session_id,
            action,
            target_stored_name or "*",
        )
        return
    except Exception as exc:
        logger.exception(
            "Translation websocket failed. session_id=%s action=%s target=%s",
            session_id,
            action,
            target_stored_name or "*",
        )
        try:
            await websocket.send_json({"event": "error", "message": str(exc)})
        except Exception:
            logger.exception("Failed to send websocket error message. session_id=%s action=%s", session_id, action)
    finally:
        if not busy_cleared:
            translator_engine.clear_session_busy(session_id)


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("APP_BACKEND_HOST") or "127.0.0.1"
    port = int(os.getenv("APP_BACKEND_PORT") or "8000")
    uvicorn.run("main:app", host=host, port=port, reload=False)
