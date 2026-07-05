from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import uvicorn

from logging_config import configure_rotating_file_logging
from runtime_paths import resolve_app_paths


def resolve_backend_dir() -> Path:
    override = os.getenv("APP_CODE_DIR")
    if override:
        return Path(override).resolve()
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path.cwd()))
        candidate = bundle_root / "backend"
        if candidate.exists():
            return candidate.resolve()
        return bundle_root.resolve()
    return Path(__file__).resolve().parent


def configure_logging() -> Path:
    backend_dir = resolve_backend_dir()
    paths = resolve_app_paths(backend_dir)
    log_path = paths.backend_log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    configure_rotating_file_logging(log_path)
    return log_path


def main() -> None:
    log_path = configure_logging()
    host = os.getenv("APP_BACKEND_HOST") or "127.0.0.1"
    port = int(os.getenv("APP_BACKEND_PORT") or "8000")
    base_dir = resolve_backend_dir()
    os.chdir(base_dir)
    logging.getLogger("manga_translator.desktop").info("Starting backend on %s:%s (log: %s)", host, port, log_path)
    uvicorn.run("main:app", host=host, port=port, reload=False, access_log=True)


if __name__ == "__main__":
    main()
