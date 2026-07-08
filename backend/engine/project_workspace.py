from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from runtime_paths import AppPaths


class InvalidStorageIdentifierError(ValueError):
    pass


class ProjectWorkspace:
    PROJECT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")

    def __init__(self, paths: AppPaths):
        self.paths = paths
        self.projects_root = paths.projects_dir
        self.project_index_path = paths.project_index_path
        self.output_root = paths.output_dir
        self.temp_dir = paths.cache_dir
        self.logs_dir = paths.logs_dir

    def validated_project_id(self, project_id: str) -> str:
        normalized = str(project_id or "")
        if normalized in {".", ".."} or not self.PROJECT_ID_PATTERN.fullmatch(normalized):
            raise InvalidStorageIdentifierError("项目标识无效，请刷新后重试。")
        return normalized

    def validated_page_id(self, page_id: str) -> str:
        normalized = str(page_id or "")
        if (
            not normalized
            or len(normalized) > 255
            or normalized in {".", ".."}
            or "\x00" in normalized
            or "/" in normalized
            or "\\" in normalized
            or Path(normalized).name != normalized
        ):
            raise InvalidStorageIdentifierError("页面标识无效，请刷新后重试。")
        return normalized

    def safe_storage_child(self, root: Path, name: str, *, label: str) -> Path:
        resolved_root = root.resolve()
        candidate = (resolved_root / name).resolve()
        if candidate == resolved_root or resolved_root not in candidate.parents:
            raise InvalidStorageIdentifierError(f"{label}无效，请刷新后重试。")
        return candidate

    def project_dir(self, project_id: str) -> Path:
        return self.safe_storage_child(
            self.projects_root,
            self.validated_project_id(project_id),
            label="项目标识",
        )

    def project_manifest_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "project.json"

    def project_session_state_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "session.json"

    def project_output_dir(self, project_id: str) -> Path:
        return self.safe_storage_child(
            self.output_root,
            self.validated_project_id(project_id),
            label="项目标识",
        )

    def project_source_dir(self, project_id: str) -> Path:
        return self.project_output_dir(project_id) / "source"

    def project_translated_dir(self, project_id: str) -> Path:
        return self.project_output_dir(project_id) / "translated"

    def project_snapshots_dir(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "snapshots"

    def project_pages_dir(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "pages"

    def project_page_dir(self, project_id: str, page_id: str) -> Path:
        return self.safe_storage_child(
            self.project_pages_dir(project_id),
            self.validated_page_id(page_id),
            label="页面标识",
        )

    def project_page_document_path(self, project_id: str, page_id: str) -> Path:
        return self.project_page_dir(project_id, page_id) / "page_document.json"

    def translation_request_debug_path(self, project_id: str) -> Path:
        normalized_project_id = self.validated_project_id(project_id)
        return self.safe_storage_child(
            self.temp_dir,
            f"{normalized_project_id}_translation-request-debug.jsonl",
            label="项目标识",
        )

    def project_temp_path(self, project_id: str, suffix: str) -> Path:
        normalized_project_id = self.validated_project_id(project_id)
        normalized_suffix = self.validated_page_id(suffix)
        return self.safe_storage_child(
            self.temp_dir,
            f"{normalized_project_id}_{normalized_suffix}",
            label="项目临时路径",
        )

    def project_log_path(self, project_id: str, suffix: str) -> Path:
        normalized_project_id = self.validated_project_id(project_id)
        normalized_suffix = self.validated_page_id(suffix)
        task_log_dir = self.safe_storage_child(
            self.logs_dir / "tasks",
            normalized_project_id,
            label="项目日志目录",
        )
        task_log_dir.mkdir(parents=True, exist_ok=True)
        return self.safe_storage_child(task_log_dir, normalized_suffix, label="项目日志文件")

    def read_json_file(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def read_jsonl_file(self, path: Path) -> list[Any]:
        if not path.exists():
            return []
        rows: list[Any] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        rows.append({"type": "unparsed_line", "raw": line})
        except Exception:
            return []
        return rows

    def write_json_file(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=f"{path.stem}_", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(temp_path, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.remove(temp_path)

    def page_document_region_count(self, project_id: str, page_id: str) -> int:
        try:
            payload = self.read_json_file(self.project_page_document_path(project_id, page_id), {})
        except InvalidStorageIdentifierError:
            return 0
        regions = payload.get("regions") if isinstance(payload, dict) else None
        if not isinstance(regions, list):
            return 0
        return sum(1 for region in regions if isinstance(region, dict))

    def project_region_count(self, project_id: str, session: dict[str, Any]) -> int:
        total = 0
        for image in session.get("source_images") or []:
            if not isinstance(image, dict):
                continue
            stored_name = str(image.get("stored_name") or "").strip()
            if stored_name:
                total += self.page_document_region_count(project_id, stored_name)
        return total

    def read_snapshot_manifests(self, project_id: str) -> list[dict[str, Any]]:
        snapshots_dir = self.project_snapshots_dir(project_id)
        manifests: list[dict[str, Any]] = []
        if not snapshots_dir.exists():
            return manifests

        for path in sorted(snapshots_dir.glob("*.json")):
            payload = self.read_json_file(path, {})
            if isinstance(payload, dict):
                payload["_path"] = str(path)
                manifests.append(payload)

        manifests.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return manifests

    def write_project_index(self, summaries: list[dict[str, Any]]) -> None:
        summaries.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        self.write_json_file(self.project_index_path, summaries)

    def refresh_project_index_entry(self, project_summary: dict[str, Any]) -> None:
        existing = self.read_json_file(self.project_index_path, [])
        next_items = [
            item
            for item in existing
            if isinstance(item, dict) and str(item.get("project_id") or "") != project_summary["project_id"]
        ]
        next_items.append(project_summary)
        self.write_project_index(next_items)
