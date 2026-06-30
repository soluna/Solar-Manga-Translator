from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


APP_NAME = "Solar-Manga-Translator"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _default_user_data_dir() -> Path:
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if base:
            return Path(base) / APP_NAME
    if sys_platform() == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def sys_platform() -> str:
    return os.sys.platform


def _read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
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


@dataclass(slots=True)
class AppPaths:
    code_dir: Path
    app_data_dir: Path
    models_dir: Path
    output_dir: Path
    logs_dir: Path
    cache_dir: Path
    config_dir: Path

    @property
    def projects_dir(self) -> Path:
        return self.app_data_dir / "projects"

    @property
    def project_index_path(self) -> Path:
        return self.projects_dir / "project_index.json"

    @property
    def cache_uploads_dir(self) -> Path:
        return self.cache_dir / "uploads"

    @property
    def cache_extracted_dir(self) -> Path:
        return self.cache_dir / "extracted"

    @property
    def migration_state_path(self) -> Path:
        return self.config_dir / "migration.json"

    @property
    def settings_path(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def backend_log_path(self) -> Path:
        return self.logs_dir / "backend.log"

    @property
    def user_fonts_dir(self) -> Path:
        return self.app_data_dir / "fonts"

    @property
    def legacy_temp_uploads_dir(self) -> Path:
        return self.code_dir / "temp_uploads"

    @property
    def legacy_output_dir(self) -> Path:
        return self.code_dir / "output_images"

    @property
    def legacy_model_dirs(self) -> list[Path]:
        return [
            self.code_dir / "models",
            self.code_dir.parent / "models",
        ]

    def ensure_directories(self) -> None:
        for path in (
            self.app_data_dir,
            self.models_dir,
            self.output_dir,
            self.logs_dir,
            self.cache_dir,
            self.config_dir,
            self.user_fonts_dir,
            self.projects_dir,
            self.cache_uploads_dir,
            self.cache_extracted_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def load_settings(self) -> dict[str, Any]:
        payload = _read_json_file(self.settings_path, {})
        return payload if isinstance(payload, dict) else {}

    def save_settings(self, payload: dict[str, Any]) -> None:
        _write_json_file(self.settings_path, payload)

    def load_migration_state(self) -> dict[str, Any]:
        payload = _read_json_file(self.migration_state_path, {})
        return payload if isinstance(payload, dict) else {}

    def save_migration_state(self, payload: dict[str, Any]) -> None:
        _write_json_file(self.migration_state_path, payload)

    def _dir_size_bytes(self, path: Path) -> int:
        if not path.exists():
            return 0
        total = 0
        for child in path.rglob("*"):
            with contextlib.suppress(OSError):
                if child.is_file():
                    total += child.stat().st_size
        return total

    def legacy_status(self) -> dict[str, Any]:
        migration_state = self.load_migration_state()
        legacy_projects = self.legacy_temp_uploads_dir / "projects"
        legacy_index = self.legacy_temp_uploads_dir / "project_index.json"
        legacy_output = self.legacy_output_dir
        legacy_models = [path for path in self.legacy_model_dirs if path.exists()]

        has_legacy_projects = legacy_projects.exists() and any(legacy_projects.iterdir())
        has_legacy_index = legacy_index.exists()
        has_legacy_output = legacy_output.exists() and any(legacy_output.iterdir())
        has_legacy_models = any(path.exists() and any(path.iterdir()) for path in legacy_models)
        needed = any((has_legacy_projects, has_legacy_index, has_legacy_output, has_legacy_models))

        return {
            "needed": bool(needed and migration_state.get("status") not in {"completed", "skipped"}),
            "status": str(migration_state.get("status") or "pending"),
            "updated_at": str(migration_state.get("updated_at") or ""),
            "legacy": {
                "projects": str(legacy_projects),
                "output": str(legacy_output),
                "models": [str(path) for path in legacy_models],
            },
            "target": {
                "app_data": str(self.app_data_dir),
                "projects": str(self.projects_dir),
                "output": str(self.output_dir),
                "models": str(self.models_dir),
                "logs": str(self.logs_dir),
                "config": str(self.config_dir),
            },
            "summary": {
                "has_legacy_projects": has_legacy_projects,
                "has_legacy_index": has_legacy_index,
                "has_legacy_output": has_legacy_output,
                "has_legacy_models": has_legacy_models,
                "legacy_bytes": (
                    self._dir_size_bytes(legacy_projects)
                    + self._dir_size_bytes(legacy_output)
                    + sum(self._dir_size_bytes(path) for path in legacy_models)
                ),
            },
        }

    def migrate_legacy(self, action: str) -> dict[str, Any]:
        normalized = str(action or "").strip().lower()
        if normalized not in {"migrate", "skip"}:
            raise ValueError("Unsupported migration action")

        if normalized == "skip":
            payload = {"status": "skipped", "updated_at": _now_iso()}
            self.save_migration_state(payload)
            return self.legacy_status()

        self.ensure_directories()
        legacy_projects = self.legacy_temp_uploads_dir / "projects"
        legacy_index = self.legacy_temp_uploads_dir / "project_index.json"
        legacy_output = self.legacy_output_dir

        if legacy_projects.exists():
            shutil.copytree(legacy_projects, self.projects_dir, dirs_exist_ok=True)
        if legacy_index.exists() and not self.project_index_path.exists():
            shutil.copy2(legacy_index, self.project_index_path)
        if legacy_output.exists():
            shutil.copytree(legacy_output, self.output_dir, dirs_exist_ok=True)

        for legacy_model_dir in self.legacy_model_dirs:
            if legacy_model_dir.exists():
                shutil.copytree(legacy_model_dir, self.models_dir, dirs_exist_ok=True)

        payload = {"status": "completed", "updated_at": _now_iso()}
        self.save_migration_state(payload)
        return self.legacy_status()


def resolve_app_paths(code_dir: Path) -> AppPaths:
    base_dir = Path(code_dir).resolve()
    app_data_dir = Path(os.getenv("APP_DATA_DIR") or _default_user_data_dir()).expanduser().resolve()
    models_dir = Path(os.getenv("APP_MODELS_DIR") or (app_data_dir / "models")).expanduser().resolve()
    output_dir = Path(os.getenv("APP_OUTPUT_DIR") or (app_data_dir / "output")).expanduser().resolve()
    logs_dir = Path(os.getenv("APP_LOG_DIR") or (app_data_dir / "logs")).expanduser().resolve()
    cache_dir = app_data_dir / "cache"
    config_dir = app_data_dir / "config"
    paths = AppPaths(
        code_dir=base_dir,
        app_data_dir=app_data_dir,
        models_dir=models_dir,
        output_dir=output_dir,
        logs_dir=logs_dir,
        cache_dir=cache_dir,
        config_dir=config_dir,
    )
    paths.ensure_directories()
    return paths
