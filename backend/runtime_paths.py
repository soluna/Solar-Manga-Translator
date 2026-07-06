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
RUNTIME_DATA_DIR_NAME = ".runtime"
LEGACY_APP_NAMES = (
    "MangaTranslator",
    "Manga Translator",
    "manga-translator",
    "manga-translator-desktop",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _default_project_data_dir(code_dir: Path) -> Path:
    return Path(code_dir).resolve().parent / RUNTIME_DATA_DIR_NAME


def _platform_app_data_bases() -> list[Path]:
    bases: list[Path] = []
    if os.name == "nt":
        for env_name in ("LOCALAPPDATA", "APPDATA"):
            value = os.getenv(env_name)
            if value:
                bases.append(Path(value))
        return bases
    if sys_platform() == "darwin":
        return [Path.home() / "Library" / "Application Support"]
    return [Path.home() / ".local" / "share"]


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


def _merge_project_index_items(left: Any, right: Any) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for raw_items in (left, right):
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            project_id = str(item.get("project_id") or "").strip()
            if not project_id:
                continue
            current = merged.get(project_id)
            if current is None or str(item.get("updated_at") or "") >= str(current.get("updated_at") or ""):
                merged[project_id] = item
    items = list(merged.values())
    items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return items


def _project_ids_from_index(index_path: Path) -> set[str]:
    payload = _read_json_file(index_path, [])
    if not isinstance(payload, list):
        return set()
    return {
        str(item.get("project_id") or "").strip()
        for item in payload
        if isinstance(item, dict) and str(item.get("project_id") or "").strip()
    }


def _project_ids_from_projects_dir(projects_dir: Path) -> set[str]:
    project_ids = set(_project_ids_from_index(projects_dir / "project_index.json"))
    if not projects_dir.exists():
        return project_ids
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        manifest = _read_json_file(project_dir / "project.json", {})
        if isinstance(manifest, dict):
            project_id = str(manifest.get("project_id") or project_dir.name).strip()
        else:
            project_id = project_dir.name
        if project_id:
            project_ids.add(project_id)
    return project_ids


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

    @property
    def legacy_app_data_dirs(self) -> list[Path]:
        candidates: list[Path] = []
        candidates.extend(self.app_data_dir.parent / name for name in LEGACY_APP_NAMES)
        for base in _platform_app_data_bases():
            candidates.extend(base / name for name in (APP_NAME, *LEGACY_APP_NAMES))

        directories: list[Path] = []
        seen: set[str] = {str(self.app_data_dir.resolve())}
        for candidate in candidates:
            with contextlib.suppress(OSError):
                normalized = str(candidate.expanduser().resolve())
                if normalized in seen:
                    continue
                seen.add(normalized)
                directories.append(candidate.expanduser())
        return directories

    def ensure_directories(self) -> None:
        for path in (
            self.app_data_dir,
            self.models_dir,
            self.output_dir,
            self.logs_dir,
            self.cache_dir,
            self.config_dir,
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
        legacy_app_data = [
            {
                "app_data": path,
                "projects": path / "projects",
                "project_index": path / "projects" / "project_index.json",
                "output": path / "output",
                "models": path / "models",
                "fonts": path / "fonts",
                "settings": path / "config" / "settings.json",
            }
            for path in self.legacy_app_data_dirs
        ]
        existing_legacy_app_data = [
            source for source in legacy_app_data if source["app_data"].exists()
        ]

        has_legacy_projects = legacy_projects.exists() and any(legacy_projects.iterdir())
        has_legacy_index = legacy_index.exists()
        has_legacy_output = legacy_output.exists() and any(legacy_output.iterdir())
        has_legacy_models = any(path.exists() and any(path.iterdir()) for path in legacy_models)
        has_legacy_app_projects = any(
            source["projects"].exists() and any(source["projects"].iterdir())
            for source in existing_legacy_app_data
        )
        has_legacy_app_output = any(
            source["output"].exists() and any(source["output"].iterdir())
            for source in existing_legacy_app_data
        )
        has_legacy_app_models = any(
            source["models"].exists() and any(source["models"].iterdir())
            for source in existing_legacy_app_data
        )
        has_legacy_app_fonts = any(
            source["fonts"].exists() and any(source["fonts"].iterdir())
            for source in existing_legacy_app_data
        )
        has_legacy_app_settings = any(
            source["settings"].exists()
            for source in existing_legacy_app_data
        )
        target_project_ids = _project_ids_from_projects_dir(self.projects_dir)
        legacy_project_ids = set(_project_ids_from_index(legacy_index))
        legacy_project_ids.update(_project_ids_from_projects_dir(legacy_projects))
        for source in existing_legacy_app_data:
            legacy_project_ids.update(_project_ids_from_projects_dir(source["projects"]))
        has_unmigrated_projects = bool(legacy_project_ids - target_project_ids)
        has_any_legacy = any((
            has_legacy_projects,
            has_legacy_index,
            has_legacy_output,
            has_legacy_models,
            has_legacy_app_projects,
            has_legacy_app_output,
            has_legacy_app_models,
            has_legacy_app_fonts,
            has_legacy_app_settings,
        ))
        migration_finished = migration_state.get("status") in {"completed", "skipped"}
        needed = bool(has_any_legacy and (not migration_finished or has_unmigrated_projects))

        return {
            "needed": needed,
            "status": str(migration_state.get("status") or "pending"),
            "updated_at": str(migration_state.get("updated_at") or ""),
            "legacy": {
                "projects": str(legacy_projects),
                "output": str(legacy_output),
                "models": [str(path) for path in legacy_models],
                "app_data": [
                    {
                        "path": str(source["app_data"]),
                        "projects": str(source["projects"]),
                        "output": str(source["output"]),
                        "fonts": str(source["fonts"]),
                    }
                    for source in existing_legacy_app_data
                ],
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
                "has_legacy_app_projects": has_legacy_app_projects,
                "has_legacy_app_output": has_legacy_app_output,
                "has_legacy_app_models": has_legacy_app_models,
                "has_legacy_app_fonts": has_legacy_app_fonts,
                "has_legacy_app_settings": has_legacy_app_settings,
                "has_unmigrated_projects": has_unmigrated_projects,
                "legacy_bytes": (
                    self._dir_size_bytes(legacy_projects)
                    + self._dir_size_bytes(legacy_output)
                    + sum(self._dir_size_bytes(path) for path in legacy_models)
                    + sum(
                        self._dir_size_bytes(source["projects"])
                        + self._dir_size_bytes(source["output"])
                        + self._dir_size_bytes(source["models"])
                        + self._dir_size_bytes(source["fonts"])
                        for source in existing_legacy_app_data
                    )
                ),
            },
        }

    def _merge_project_index_file(self, source_index_path: Path) -> None:
        if not source_index_path.exists():
            return
        source_items = _read_json_file(source_index_path, [])
        target_items = _read_json_file(self.project_index_path, [])
        merged = _merge_project_index_items(target_items, source_items)
        if merged:
            _write_json_file(self.project_index_path, merged)

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
        self._merge_project_index_file(legacy_index)
        if legacy_output.exists():
            shutil.copytree(legacy_output, self.output_dir, dirs_exist_ok=True)

        for legacy_model_dir in self.legacy_model_dirs:
            if legacy_model_dir.exists():
                shutil.copytree(legacy_model_dir, self.models_dir, dirs_exist_ok=True)

        for legacy_app_data_dir in self.legacy_app_data_dirs:
            if not legacy_app_data_dir.exists():
                continue
            legacy_app_projects = legacy_app_data_dir / "projects"
            legacy_app_output = legacy_app_data_dir / "output"
            legacy_app_models = legacy_app_data_dir / "models"
            legacy_app_fonts = legacy_app_data_dir / "fonts"
            legacy_app_settings = legacy_app_data_dir / "config" / "settings.json"

            if legacy_app_projects.exists():
                self._merge_project_index_file(legacy_app_projects / "project_index.json")
                shutil.copytree(
                    legacy_app_projects,
                    self.projects_dir,
                    dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(self.project_index_path.name),
                )
            if legacy_app_output.exists():
                shutil.copytree(legacy_app_output, self.output_dir, dirs_exist_ok=True)
            if legacy_app_models.exists():
                shutil.copytree(legacy_app_models, self.models_dir, dirs_exist_ok=True)
            if legacy_app_fonts.exists():
                font_root = Path(os.getenv("APP_FONT_DIR") or self.user_fonts_dir).expanduser().resolve()
                custom_font_dir = font_root / "custom"
                custom_font_dir.mkdir(parents=True, exist_ok=True)
                legacy_custom_dir = legacy_app_fonts / "custom"
                if legacy_custom_dir.exists():
                    shutil.copytree(legacy_custom_dir, custom_font_dir, dirs_exist_ok=True)
                else:
                    shutil.copytree(
                        legacy_app_fonts,
                        custom_font_dir,
                        dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("system", "builtin"),
                    )
            if legacy_app_settings.exists() and not self.settings_path.exists():
                self.settings_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy_app_settings, self.settings_path)

        payload = {"status": "completed", "updated_at": _now_iso()}
        self.save_migration_state(payload)
        return self.legacy_status()


def resolve_app_paths(code_dir: Path) -> AppPaths:
    base_dir = Path(code_dir).resolve()
    app_data_dir = Path(
        os.getenv("APP_DATA_DIR") or _default_project_data_dir(base_dir),
    ).expanduser().resolve()
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
