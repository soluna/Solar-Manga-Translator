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


def _current_app_version() -> str:
    return str(os.getenv("APP_VERSION") or "dev").strip() or "dev"


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
    def temp_dir(self) -> Path:
        return self.app_data_dir / "temp"

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
    def legacy_project_custom_fonts_dir(self) -> Path:
        return self.code_dir.parent / "fonts" / "custom"

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

    @property
    def legacy_temp_cache_dirs(self) -> list[Path]:
        candidates: list[Path] = []
        raw_bases = str(os.getenv("APP_LEGACY_TEMP_DIRS") or "")
        for raw_base in raw_bases.split(os.pathsep):
            value = raw_base.strip()
            if not value:
                continue
            base = Path(value).expanduser()
            candidates.append(
                base
                if base.name.lower() == "manga-image-translator"
                else base / "manga-image-translator"
            )

        directories: list[Path] = []
        seen: set[str] = {os.path.normcase(str(self.temp_dir.resolve()))}
        for candidate in candidates:
            with contextlib.suppress(OSError):
                normalized = os.path.normcase(str(candidate.resolve()))
                if normalized in seen:
                    continue
                seen.add(normalized)
                directories.append(candidate)
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
            self.temp_dir,
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
        legacy_project_fonts = self.legacy_project_custom_fonts_dir
        legacy_temp_cache = [
            path for path in self.legacy_temp_cache_dirs if path.exists()
        ]
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
        has_legacy_project_fonts = (
            legacy_project_fonts.is_dir()
            and any(legacy_project_fonts.iterdir())
        )
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
        has_legacy_app_runtime = any(
            source["app_data"].is_dir() and any(source["app_data"].iterdir())
            for source in existing_legacy_app_data
        )
        has_legacy_temp_cache = any(
            path.is_dir() and any(path.iterdir())
            for path in legacy_temp_cache
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
            has_legacy_project_fonts,
            has_legacy_app_projects,
            has_legacy_app_output,
            has_legacy_app_models,
            has_legacy_app_fonts,
            has_legacy_app_settings,
            has_legacy_app_runtime,
            has_legacy_temp_cache,
        ))
        migration_status = str(migration_state.get("status") or "pending")
        if migration_status == "completed":
            needed = bool(
                has_any_legacy
                and (
                    has_unmigrated_projects
                    or str(migration_state.get("cleanup_status") or "") == "partial"
                )
            )
        elif migration_status == "skipped":
            needed = bool(
                has_any_legacy
                and str(migration_state.get("version") or "") != _current_app_version()
            )
        else:
            needed = bool(has_any_legacy)

        return {
            "needed": needed,
            "status": migration_status,
            "version": str(migration_state.get("version") or ""),
            "updated_at": str(migration_state.get("updated_at") or ""),
            "cleanup": {
                "status": str(migration_state.get("cleanup_status") or "not_requested"),
                "deleted_paths": list(migration_state.get("deleted_paths") or []),
                "failed_paths": list(migration_state.get("failed_paths") or []),
            },
            "legacy": {
                "projects": str(legacy_projects),
                "output": str(legacy_output),
                "models": [str(path) for path in legacy_models],
                "project_fonts": str(legacy_project_fonts),
                "temp_cache": [str(path) for path in legacy_temp_cache],
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
                "cache": str(self.cache_dir),
                "temp": str(self.temp_dir),
                "fonts": str(self.user_fonts_dir),
                "config": str(self.config_dir),
            },
            "summary": {
                "has_legacy_projects": has_legacy_projects,
                "has_legacy_index": has_legacy_index,
                "has_legacy_output": has_legacy_output,
                "has_legacy_models": has_legacy_models,
                "has_legacy_project_fonts": has_legacy_project_fonts,
                "has_legacy_app_projects": has_legacy_app_projects,
                "has_legacy_app_output": has_legacy_app_output,
                "has_legacy_app_models": has_legacy_app_models,
                "has_legacy_app_fonts": has_legacy_app_fonts,
                "has_legacy_app_settings": has_legacy_app_settings,
                "has_legacy_app_runtime": has_legacy_app_runtime,
                "has_legacy_temp_cache": has_legacy_temp_cache,
                "has_unmigrated_projects": has_unmigrated_projects,
                "legacy_bytes": (
                    self._dir_size_bytes(legacy_projects)
                    + self._dir_size_bytes(legacy_output)
                    + sum(self._dir_size_bytes(path) for path in legacy_models)
                    + self._dir_size_bytes(legacy_project_fonts)
                    + sum(
                        self._dir_size_bytes(source["app_data"])
                        for source in existing_legacy_app_data
                    )
                    + sum(self._dir_size_bytes(path) for path in legacy_temp_cache)
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

    def _cleanup_legacy_directories(self) -> tuple[list[str], list[str]]:
        candidates = [
            self.legacy_temp_uploads_dir,
            self.legacy_output_dir,
            *self.legacy_model_dirs,
            *self.legacy_app_data_dirs,
            *self.legacy_temp_cache_dirs,
        ]
        current_root = self.app_data_dir.resolve()
        unique: dict[str, Path] = {}
        for candidate in candidates:
            with contextlib.suppress(OSError):
                resolved = candidate.resolve()
                if resolved == current_root or current_root in resolved.parents:
                    continue
                unique[os.path.normcase(str(resolved))] = resolved

        deleted: list[str] = []
        failed: list[str] = []
        for target in sorted(
            unique.values(),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            if not target.exists():
                continue
            try:
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
                deleted.append(str(target))
            except OSError:
                failed.append(str(target))
        return deleted, failed

    def _archive_legacy_settings(
        self,
        source_path: Path,
        source_root: Path,
    ) -> None:
        if not source_path.is_file():
            return
        if self.settings_path.is_file():
            with contextlib.suppress(OSError):
                if source_path.read_bytes() == self.settings_path.read_bytes():
                    return
        archive_dir = self.config_dir / "legacy-settings"
        archive_dir.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in source_root.name
        ).strip("-") or "legacy"
        target = archive_dir / f"{safe_name}.json"
        index = 2
        while target.exists():
            with contextlib.suppress(OSError):
                if target.read_bytes() == source_path.read_bytes():
                    return
            target = archive_dir / f"{safe_name}-{index}.json"
            index += 1
        shutil.copy2(source_path, target)

    def migrate_legacy(self, action: str) -> dict[str, Any]:
        normalized = str(action or "").strip().lower()
        if normalized not in {"migrate", "migrate_clean", "skip"}:
            raise ValueError("Unsupported migration action")

        if normalized == "skip":
            payload = {
                "status": "skipped",
                "version": _current_app_version(),
                "updated_at": _now_iso(),
            }
            self.save_migration_state(payload)
            return self.legacy_status()

        self.ensure_directories()
        legacy_project_ids: set[str] = set()
        legacy_projects = self.legacy_temp_uploads_dir / "projects"
        legacy_index = self.legacy_temp_uploads_dir / "project_index.json"
        legacy_output = self.legacy_output_dir
        legacy_project_ids.update(_project_ids_from_index(legacy_index))
        legacy_project_ids.update(_project_ids_from_projects_dir(legacy_projects))

        if legacy_projects.exists():
            shutil.copytree(legacy_projects, self.projects_dir, dirs_exist_ok=True)
        self._merge_project_index_file(legacy_index)
        if legacy_output.exists():
            shutil.copytree(legacy_output, self.output_dir, dirs_exist_ok=True)

        for legacy_model_dir in self.legacy_model_dirs:
            if legacy_model_dir.exists():
                shutil.copytree(legacy_model_dir, self.models_dir, dirs_exist_ok=True)

        if self.legacy_project_custom_fonts_dir.exists():
            shutil.copytree(
                self.legacy_project_custom_fonts_dir,
                self.user_fonts_dir / "custom",
                dirs_exist_ok=True,
            )

        for legacy_app_data_dir in self.legacy_app_data_dirs:
            if not legacy_app_data_dir.exists():
                continue
            legacy_app_projects = legacy_app_data_dir / "projects"
            legacy_app_output = legacy_app_data_dir / "output"
            legacy_app_models = legacy_app_data_dir / "models"
            legacy_app_fonts = legacy_app_data_dir / "fonts"
            legacy_app_settings = legacy_app_data_dir / "config" / "settings.json"
            legacy_project_ids.update(
                _project_ids_from_projects_dir(legacy_app_projects),
            )

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
                custom_font_dir = self.user_fonts_dir / "custom"
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
            if legacy_app_settings.exists():
                if not self.settings_path.exists():
                    self.settings_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(legacy_app_settings, self.settings_path)
                elif normalized == "migrate_clean":
                    self._archive_legacy_settings(
                        legacy_app_settings,
                        legacy_app_data_dir,
                    )

        missing_projects = legacy_project_ids - _project_ids_from_projects_dir(
            self.projects_dir,
        )
        if missing_projects:
            raise RuntimeError(
                "Legacy project verification failed: "
                + ", ".join(sorted(missing_projects))
            )

        deleted_paths: list[str] = []
        failed_paths: list[str] = []
        cleanup_status = "preserved"
        if normalized == "migrate_clean":
            deleted_paths, failed_paths = self._cleanup_legacy_directories()
            cleanup_status = "partial" if failed_paths else "completed"

        payload = {
            "status": "completed",
            "version": _current_app_version(),
            "updated_at": _now_iso(),
            "cleanup_status": cleanup_status,
            "deleted_paths": deleted_paths,
            "failed_paths": failed_paths,
        }
        self.save_migration_state(payload)
        return self.legacy_status()


def resolve_app_paths(code_dir: Path) -> AppPaths:
    base_dir = Path(code_dir).resolve()
    app_data_dir = Path(
        os.getenv("APP_DATA_DIR") or _default_project_data_dir(base_dir),
    ).expanduser().resolve()
    models_dir = app_data_dir / "models"
    output_dir = app_data_dir / "output"
    logs_dir = app_data_dir / "logs"
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


def _cache_environment(paths: AppPaths) -> dict[str, str]:
    def cache_path(*parts: str) -> str:
        return str(paths.cache_dir.joinpath(*parts))

    return {
        "APP_DATA_DIR": str(paths.app_data_dir),
        "APP_MODELS_DIR": str(paths.models_dir),
        "APP_OUTPUT_DIR": str(paths.output_dir),
        "APP_LOG_DIR": str(paths.logs_dir),
        "APP_CACHE_DIR": str(paths.cache_dir),
        "APP_TEMP_DIR": str(paths.temp_dir),
        "APP_FONT_DIR": str(paths.user_fonts_dir),
        "TEMP": str(paths.temp_dir),
        "TMP": str(paths.temp_dir),
        "TMPDIR": str(paths.temp_dir),
        "XDG_CACHE_HOME": cache_path("external"),
        "HF_HOME": cache_path("huggingface"),
        "HF_HUB_CACHE": cache_path("huggingface", "hub"),
        "HUGGINGFACE_HUB_CACHE": cache_path("huggingface", "hub"),
        "HF_ASSETS_CACHE": cache_path("huggingface", "assets"),
        "HF_XET_CACHE": cache_path("huggingface", "xet"),
        "HF_DATASETS_CACHE": cache_path("huggingface", "datasets"),
        "TRANSFORMERS_CACHE": cache_path("huggingface", "transformers"),
        "TORCH_HOME": cache_path("torch"),
        "TORCH_EXTENSIONS_DIR": cache_path("torch", "extensions"),
        "TORCHINDUCTOR_CACHE_DIR": cache_path("torch", "inductor"),
        "TRITON_CACHE_DIR": cache_path("triton"),
        "MPLCONFIGDIR": cache_path("matplotlib"),
        "NUMBA_CACHE_DIR": cache_path("numba"),
        "CUDA_CACHE_PATH": cache_path("cuda"),
        "PYTHONPYCACHEPREFIX": cache_path("python-bytecode"),
        "PIP_CACHE_DIR": cache_path("pip"),
        "npm_config_cache": cache_path("npm"),
        "ELECTRON_CACHE": cache_path("electron-downloads"),
        "ELECTRON_BUILDER_CACHE": cache_path("electron-builder"),
    }


def configure_runtime_environment(paths: AppPaths) -> dict[str, str]:
    """Route process-owned temporary and cache writes into one app-data root."""
    paths.ensure_directories()
    bundled_fonts_dir = paths.code_dir.parent / "fonts" / "system"
    runtime_system_fonts_dir = paths.user_fonts_dir / "system"
    runtime_system_fonts_dir.mkdir(parents=True, exist_ok=True)
    if bundled_fonts_dir.is_dir():
        shutil.copytree(
            bundled_fonts_dir,
            runtime_system_fonts_dir,
            dirs_exist_ok=True,
        )
    legacy_candidates: list[str] = []
    raw_legacy = str(os.getenv("APP_LEGACY_TEMP_DIRS") or "")
    if raw_legacy:
        legacy_candidates.extend(item for item in raw_legacy.split(os.pathsep) if item)
    legacy_candidates.extend(
        str(os.getenv(name) or "").strip()
        for name in ("TEMP", "TMP", "TMPDIR")
    )
    runtime_root = paths.app_data_dir.resolve()
    legacy_temp_dirs: list[str] = []
    seen: set[str] = set()
    for raw_candidate in legacy_candidates:
        if not raw_candidate:
            continue
        candidate = Path(raw_candidate).expanduser()
        with contextlib.suppress(OSError):
            resolved = candidate.resolve()
            if resolved == runtime_root or runtime_root in resolved.parents:
                continue
            normalized = os.path.normcase(str(resolved))
            if normalized in seen:
                continue
            seen.add(normalized)
            legacy_temp_dirs.append(str(resolved))

    environment = _cache_environment(paths)
    if legacy_temp_dirs:
        environment["APP_LEGACY_TEMP_DIRS"] = os.pathsep.join(legacy_temp_dirs)
    os.environ.update(environment)
    tempfile.tempdir = str(paths.temp_dir)
    return environment
