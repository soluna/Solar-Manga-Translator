from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from domain.project_state import CorruptProjectStateError, ProjectState, ProjectStateError
from runtime_paths import AppPaths


logger = logging.getLogger(__name__)


class InvalidStorageIdentifierError(ValueError):
    pass


class CorruptSnapshotArtifactError(ProjectStateError):
    pass


class CorruptProjectArtifactError(ProjectStateError):
    pass


class ProjectHeadConflictError(ProjectStateError):
    def __init__(
        self,
        *,
        expected_generation: int,
        actual_generation: int,
        expected_revision_id: str = "",
        actual_revision_id: str = "",
    ):
        self.expected_generation = expected_generation
        self.actual_generation = actual_generation
        self.expected_revision_id = expected_revision_id
        self.actual_revision_id = actual_revision_id
        super().__init__(
            "项目当前版本已变化，请刷新后重试。"
        )


@dataclass(frozen=True)
class CommandBase:
    project_id: str
    page_id: str
    head: dict[str, Any] | None
    head_generation: int
    head_revision_id: str
    state_document: dict[str, Any]
    project_manifest: dict[str, Any]
    page_document: dict[str, Any]
    page_revision: int


@dataclass(frozen=True)
class ProjectCommandBase:
    project_id: str
    head: dict[str, Any] | None
    head_generation: int
    head_revision_id: str
    state_document: dict[str, Any]
    project_manifest: dict[str, Any]
    page_documents: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class PageWorkingSet:
    base: CommandBase
    root: Path
    source_dir: Path
    translated_dir: Path
    cache_dir: Path
    canonical_source_dir: Path | None
    canonical_translated_dir: Path | None
    canonical_cache_dir: Path | None
    legacy_runtime_session: dict[str, Any] | None


@dataclass(frozen=True)
class ProjectWorkingSet:
    base: ProjectCommandBase
    root: Path
    source_dir: Path
    translated_dir: Path
    cache_dir: Path
    archive_dir: Path
    canonical_source_dir: Path | None
    canonical_translated_dir: Path | None
    canonical_cache_dir: Path | None
    canonical_archive_path: Path
    initial_state_document: dict[str, Any]
    page_checkpoints: Mapping[str, str]
    pending_restored: bool
    action: str
    resume_fingerprint: str


@dataclass(frozen=True)
class PreparedHeadUpdate:
    state_document: dict[str, Any]
    project_manifest: dict[str, Any]
    page_documents: dict[str, dict[str, Any]]
    artifact_files: dict[str, Path]
    replace_prefixes: tuple[str, ...]
    remove_logical_paths: set[str]
    runtime_session: dict[str, Any]
    execution_extras: dict[str, Any]
    snapshot_document: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProjectHeadCommitResult:
    head: dict[str, Any]
    warnings: tuple[str, ...]
    runtime_session: dict[str, Any]


class ProjectWorkspace:
    PROJECT_INDEX_FIELDS = (
        "project_id",
        "title",
        "note",
        "review_mode",
        "created_at",
        "updated_at",
        "page_count",
        "region_count",
        "workflow_stage",
        "cover_image",
        "latest_snapshot_id",
        "latest_snapshot_kind",
        "latest_snapshot_summary",
        "snapshot_count",
        "glossary_count",
        "archived",
        "is_busy",
        "busy_action",
    )

    PROJECT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")

    def __init__(self, paths: AppPaths):
        self.paths = paths
        self.projects_root = paths.projects_dir
        self.project_index_path = paths.project_index_path
        self.output_root = paths.output_dir
        self.temp_dir = paths.cache_dir
        self.logs_dir = paths.logs_dir
        self._head_commit_locks: dict[str, threading.RLock] = {}
        self._head_commit_locks_guard = threading.Lock()

    def _head_commit_lock(self, project_id: str) -> threading.RLock:
        normalized_project_id = self.validated_project_id(project_id)
        with self._head_commit_locks_guard:
            return self._head_commit_locks.setdefault(
                normalized_project_id,
                threading.RLock(),
            )

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

    def project_snapshot_blobs_dir(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "snapshot_blobs"

    def project_artifact_store_dir(self, project_id: str) -> Path:
        # Keep the schema-v1 directory name so existing snapshot manifests remain
        # valid while live revisions migrate lazily into the shared store.
        return self.project_snapshot_blobs_dir(project_id)

    def project_head_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "artifact_head.json"

    def project_revisions_dir(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "artifact_revisions"

    def project_pending_artifact_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "pending_artifact_set.json"

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

    def read_project_session_document(self, project_id: str) -> dict[str, Any] | None:
        head_payload = self._read_project_head_json(project_id, "state/session.json")
        if head_payload is not None:
            if not isinstance(head_payload, dict):
                raise CorruptProjectStateError(
                    "项目状态文件已损坏：顶层内容必须是 JSON 对象。"
                )
            return head_payload
        path = self.project_session_state_path(project_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CorruptProjectStateError(
                "项目状态文件已损坏，无法安全恢复。请保留项目数据并导出诊断包。"
            ) from exc
        if not isinstance(payload, dict):
            raise CorruptProjectStateError(
                "项目状态文件已损坏：顶层内容必须是 JSON 对象。"
            )
        return payload

    def read_project_manifest(self, project_id: str) -> dict[str, Any]:
        head_payload = self._read_project_head_json(project_id, "project/project.json")
        if head_payload is not None:
            if not isinstance(head_payload, dict):
                raise CorruptProjectArtifactError("项目清单 revision 已损坏，无法安全读取。")
            return head_payload
        payload = self.read_json_file(self.project_manifest_path(project_id), {})
        return payload if isinstance(payload, dict) else {}

    def read_project_page_document(self, project_id: str, page_id: str) -> dict[str, Any]:
        normalized_page_id = self.validated_page_id(page_id)
        logical_path = f"pages/{normalized_page_id}/page_document.json"
        head_payload = self._read_project_head_json(project_id, logical_path)
        if head_payload is not None:
            if not isinstance(head_payload, dict):
                raise CorruptProjectArtifactError("页面文档 revision 已损坏，无法安全读取。")
            return head_payload
        payload = self.read_json_file(
            self.project_page_document_path(project_id, normalized_page_id),
            {},
        )
        return payload if isinstance(payload, dict) else {}

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

    def read_project_head(self, project_id: str) -> dict[str, Any] | None:
        path = self.project_head_path(project_id)
        if not path.exists():
            return None
        payload = self.read_json_file(path, None)
        if not isinstance(payload, dict):
            raise CorruptProjectArtifactError("项目当前版本指针已损坏，无法安全恢复。")
        if payload.get("schema_version") != 1:
            raise CorruptProjectArtifactError("项目当前版本格式不受支持，请升级应用后重试。")
        if not isinstance(payload.get("generation"), int) or int(payload["generation"]) < 1:
            raise CorruptProjectArtifactError("项目当前版本 generation 无效，无法安全恢复。")
        if not isinstance(payload.get("files"), dict):
            raise CorruptProjectArtifactError("项目当前版本缺少文件引用，无法安全恢复。")
        return payload

    def read_pending_artifact_set(self, project_id: str) -> dict[str, Any] | None:
        normalized_project_id = self.validated_project_id(project_id)
        path = self.project_pending_artifact_path(normalized_project_id)
        if not path.exists():
            return None
        payload = self.read_json_file(path, None)
        schema_version = payload.get("schema_version") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or isinstance(schema_version, bool)
            or schema_version not in {1, 2}
        ):
            raise CorruptProjectArtifactError("待恢复产物清单已损坏，无法安全恢复。")
        artifact_bundle = payload.get("artifact_bundle")
        if (
            not isinstance(artifact_bundle, dict)
            or artifact_bundle.get("schema_version") != 1
            or not isinstance(artifact_bundle.get("files"), dict)
        ):
            raise CorruptProjectArtifactError("待恢复产物引用已损坏，无法安全恢复。")
        if not isinstance(payload.get("state_document"), dict):
            raise CorruptProjectArtifactError("待恢复项目状态已损坏，无法安全恢复。")
        if str(payload.get("project_id") or "") != normalized_project_id:
            raise CorruptProjectArtifactError("待恢复产物所属项目无效，无法安全恢复。")
        if not isinstance(payload.get("action"), str) or not payload["action"]:
            raise CorruptProjectArtifactError("待恢复产物命令无效，无法安全恢复。")
        if (
            not isinstance(payload.get("resume_fingerprint"), str)
            or not payload["resume_fingerprint"]
        ):
            raise CorruptProjectArtifactError("待恢复产物指纹无效，无法安全恢复。")
        base_generation = payload.get("base_head_generation")
        base_revision_id = payload.get("base_head_revision_id")
        if (
            isinstance(base_generation, bool)
            or not isinstance(base_generation, int)
            or base_generation < 0
            or not isinstance(base_revision_id, str)
            or (base_generation > 0 and not base_revision_id)
        ):
            raise CorruptProjectArtifactError("待恢复产物基础版本无效，无法安全恢复。")
        try:
            if "state_validated" in payload and payload.get("state_validated") is not True:
                raise CorruptProjectArtifactError(
                    "待恢复项目状态校验标记已损坏，无法安全恢复。"
                )
            # Markerless schema-v1 Pending sets remain readable, but their
            # contents receive the same validation as newly written sets.
            # Compatibility must not turn a corrupt, non-matching Pending set
            # into data that is silently ignored by the next command.
            pending_state = ProjectState.load(
                payload["state_document"],
                expected_project_id=normalized_project_id,
            )
            for logical_path, metadata in artifact_bundle["files"].items():
                self._validated_pending_logical_path(logical_path)
                self._read_artifact_bytes(normalized_project_id, metadata)
            page_checkpoints = self._normalize_pending_page_checkpoints(
                payload,
                pending_state.workflow_stage,
            )
            self._validate_pending_checkpoint_claims(
                normalized_project_id,
                page_checkpoints,
                pending_state,
                artifact_bundle["files"],
            )
        except Exception as exc:
            raise CorruptProjectArtifactError(
                "待恢复产物内容已损坏，无法安全恢复。"
            ) from exc
        normalized_payload = dict(payload)
        normalized_payload["page_checkpoints"] = page_checkpoints
        return normalized_payload

    def _normalize_pending_page_checkpoints(
        self,
        pending: dict[str, Any],
        workflow_stage: str,
    ) -> dict[str, str]:
        action = str(pending.get("action") or "")
        allowed_page_stages = {
            ("detect", "detecting"): {"detected"},
            ("detect", "detected"): {"detected"},
            ("translate", "detecting"): {"detected"},
            ("translate", "detected"): {"detected"},
            ("translate", "translating"): {"detected", "rendered", "finalized"},
            ("resume-translate", "translating"): {"rendered", "finalized"},
            ("translate-page", "translating"): {"rendered", "finalized"},
            ("rerender", "translated"): {"finalized"},
        }.get((action, workflow_stage))
        if allowed_page_stages is None:
            raise CorruptProjectArtifactError(
                "待恢复产物检查点与工作流阶段不一致，无法安全恢复。"
            )

        if pending.get("schema_version") == 1:
            raw_page_ids = pending.get("completed_page_ids")
            if not isinstance(raw_page_ids, list):
                raise CorruptProjectArtifactError(
                    "待恢复产物页面检查点无效，无法安全恢复。"
                )
            legacy_stage = {
                ("detect", "detecting"): "detected",
                ("detect", "detected"): "detected",
                ("translate", "detecting"): "detected",
                ("translate", "detected"): "detected",
                ("translate", "translating"): "rendered",
                ("resume-translate", "translating"): "rendered",
                ("translate-page", "translating"): "rendered",
                ("rerender", "translated"): "finalized",
            }[(action, workflow_stage)]
            try:
                normalized_ids = [
                    self.validated_page_id(page_id) for page_id in raw_page_ids
                ]
            except (InvalidStorageIdentifierError, TypeError) as exc:
                raise CorruptProjectArtifactError(
                    "待恢复产物页面检查点无效，无法安全恢复。"
                ) from exc
            if len(set(normalized_ids)) != len(normalized_ids):
                raise CorruptProjectArtifactError(
                    "待恢复产物页面检查点重复，无法安全恢复。"
                )
            return {
                page_id: legacy_stage for page_id in sorted(normalized_ids)
            }

        if "completed_page_ids" in pending:
            raise CorruptProjectArtifactError(
                "待恢复产物页面检查点格式混合，无法安全恢复。"
            )
        raw_checkpoints = pending.get("page_checkpoints")
        if not isinstance(raw_checkpoints, dict):
            raise CorruptProjectArtifactError(
                "待恢复产物页面检查点无效，无法安全恢复。"
            )
        normalized: dict[str, str] = {}
        try:
            for raw_page_id, raw_stage in raw_checkpoints.items():
                page_id = self.validated_page_id(raw_page_id)
                if not isinstance(raw_stage, str) or raw_stage not in allowed_page_stages:
                    raise CorruptProjectArtifactError(
                        "待恢复产物页面阶段无效，无法安全恢复。"
                    )
                normalized[page_id] = raw_stage
        except (InvalidStorageIdentifierError, TypeError) as exc:
            raise CorruptProjectArtifactError(
                "待恢复产物页面检查点无效，无法安全恢复。"
            ) from exc
        return dict(sorted(normalized.items()))

    def _validate_pending_checkpoint_claims(
        self,
        project_id: str,
        page_checkpoints: Mapping[str, str],
        state: ProjectState,
        artifact_files: dict[str, Any],
    ) -> None:
        if not page_checkpoints:
            return

        for page_id, page_stage in page_checkpoints.items():
            capabilities = state.artifact_state.page_view(page_id).capabilities
            self._validate_pending_page_cache(
                project_id,
                page_id,
                artifact_files,
            )
            if page_stage == "detected":
                if not capabilities.recognition_ready or not capabilities.blank_ready:
                    raise CorruptProjectArtifactError(
                        "待恢复识别检查点缺少已验证的页面状态或缓存产物。"
                    )
                continue

            output_name = state.translated_output_map.get(page_id)
            if (
                not capabilities.final_ready
                or not isinstance(output_name, str)
                or not output_name
                or Path(output_name).name != output_name
                or f"translated/{output_name}" not in artifact_files
            ):
                raise CorruptProjectArtifactError(
                    "待恢复翻译检查点缺少已验证的页面状态或翻译产物。"
                )
            self._validate_pending_image_artifact(
                project_id,
                artifact_files,
                f"translated/{output_name}",
            )

    def _validate_pending_page_cache(
        self,
        project_id: str,
        page_id: str,
        artifact_files: dict[str, Any],
    ) -> None:
        cache_prefix = f"cache/{page_id}"
        regions = self._read_pending_json_artifact(
            project_id,
            artifact_files,
            f"{cache_prefix}/regions.json",
        )
        if not isinstance(regions, list) or any(
            not isinstance(region, dict) for region in regions
        ):
            raise CorruptProjectArtifactError(
                "待恢复页面缓存 regions.json 无效，无法安全恢复。"
            )

        metadata = self._read_pending_json_artifact(
            project_id,
            artifact_files,
            f"{cache_prefix}/meta.json",
        )
        if not isinstance(metadata, dict):
            raise CorruptProjectArtifactError(
                "待恢复页面缓存 meta.json 无效，无法安全恢复。"
            )
        base_kind = metadata.get("base_kind")
        if base_kind not in {"inpainted", "source_no_text"}:
            raise CorruptProjectArtifactError(
                "待恢复页面缓存基础图类型无效，无法安全恢复。"
            )

        if "inpainting_region_count" in metadata:
            region_count = metadata["inpainting_region_count"]
            if (
                isinstance(region_count, bool)
                or not isinstance(region_count, int)
                or region_count != len(regions)
            ):
                raise CorruptProjectArtifactError(
                    "待恢复页面缓存区域数量无效，无法安全恢复。"
                )

        if base_kind == "inpainted":
            self._validate_pending_image_artifact(
                project_id,
                artifact_files,
                f"{cache_prefix}/inpainted.png",
            )

    def _read_pending_json_artifact(
        self,
        project_id: str,
        artifact_files: dict[str, Any],
        logical_path: str,
    ) -> Any:
        if logical_path not in artifact_files:
            raise CorruptProjectArtifactError(
                f"待恢复产物缺少 {logical_path}，无法安全恢复。"
            )
        raw_bytes = self._read_artifact_bytes(
            project_id,
            artifact_files[logical_path],
        )
        try:
            return json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise CorruptProjectArtifactError(
                f"待恢复产物中的 {logical_path} 已损坏，无法安全恢复。"
            ) from exc

    def _validate_pending_image_artifact(
        self,
        project_id: str,
        artifact_files: dict[str, Any],
        logical_path: str,
    ) -> None:
        if logical_path not in artifact_files:
            raise CorruptProjectArtifactError(
                f"待恢复产物缺少 {logical_path}，无法安全恢复。"
            )
        raw_bytes = self._read_artifact_bytes(
            project_id,
            artifact_files[logical_path],
        )
        try:
            from PIL import Image

            with Image.open(io.BytesIO(raw_bytes)) as image:
                image.verify()
            # Pillow's structural ``verify`` check can accept a truncated JPEG
            # whose pixel stream cannot actually be restored. Re-open from the
            # beginning and force a full decode before trusting the checkpoint.
            with Image.open(io.BytesIO(raw_bytes)) as image:
                if image.width <= 0 or image.height <= 0:
                    raise ValueError("image dimensions must be positive")
                image.load()
        except Exception as exc:
            raise CorruptProjectArtifactError(
                f"待恢复产物中的 {logical_path} 不是有效图片，无法安全恢复。"
            ) from exc

    def _read_bound_head_json(
        self,
        project_id: str,
        head: dict[str, Any],
        logical_path: str,
    ) -> Any | None:
        metadata = dict(head.get("files") or {}).get(logical_path)
        if metadata is None:
            return None
        raw_bytes = self._read_artifact_bytes(project_id, metadata)
        try:
            return json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise CorruptProjectArtifactError(
                f"项目绑定版本中的 {logical_path} 已损坏，无法安全读取。"
            ) from exc

    @staticmethod
    def _validated_page_revision(page_document: dict[str, Any]) -> int:
        metadata = page_document.get("metadata")
        revision = metadata.get("revision") if isinstance(metadata, dict) else None
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision <= 0
        ):
            raise CorruptProjectArtifactError(
                "Page Document revision 无效，无法安全执行页面命令。"
            )
        return revision

    def _head_page_document_paths(
        self,
        head: dict[str, Any],
    ) -> dict[str, str]:
        page_document_paths: dict[str, str] = {}
        for logical_path in head["files"]:
            if not isinstance(logical_path, str):
                raise CorruptProjectArtifactError(
                    "Project Head 文件路径无效，无法安全执行项目命令。"
                )
            try:
                parts = self._validated_snapshot_logical_path(logical_path)
            except CorruptSnapshotArtifactError as exc:
                raise CorruptProjectArtifactError(
                    "Project Head 文件路径无效，无法安全执行项目命令。"
                ) from exc
            if logical_path != "/".join(parts):
                raise CorruptProjectArtifactError(
                    "Project Head 文件路径不规范，无法安全执行项目命令。"
                )
            if parts[0] != "pages":
                continue
            if len(parts) != 3 or parts[2] != "page_document.json":
                raise CorruptProjectArtifactError(
                    "Project Head 页面文件路径无效，无法安全执行项目命令。"
                )
            try:
                page_id = self.validated_page_id(parts[1])
            except InvalidStorageIdentifierError as exc:
                raise CorruptProjectArtifactError(
                    "Project Head 页面标识无效，无法安全执行项目命令。"
                ) from exc
            if page_id in page_document_paths:
                raise CorruptProjectArtifactError(
                    "Project Head 页面文件重复，无法安全执行项目命令。"
                )
            page_document_paths[page_id] = logical_path
        return page_document_paths

    def _read_validated_command_documents(
        self,
        project_id: str,
    ) -> tuple[
        str,
        dict[str, Any] | None,
        dict[str, Any],
        dict[str, Any],
        dict[str, dict[str, Any]] | None,
    ]:
        normalized_project_id = self.validated_project_id(project_id)
        head = self.read_project_head(normalized_project_id)
        head_page_documents: dict[str, dict[str, Any]] | None = None
        if head is None:
            state_document = self.read_project_session_document(normalized_project_id)
            project_manifest = self.read_project_manifest(normalized_project_id)
        else:
            state_document = self._read_bound_head_json(
                normalized_project_id,
                head,
                "state/session.json",
            )
            project_manifest = self._read_bound_head_json(
                normalized_project_id,
                head,
                "project/project.json",
            )
            head_page_documents = {}
            for page_id, logical_path in self._head_page_document_paths(head).items():
                document = self._read_bound_head_json(
                    normalized_project_id,
                    head,
                    logical_path,
                )
                if not isinstance(document, dict):
                    raise CorruptProjectArtifactError(
                        f"项目绑定版本缺少有效的 Page Document：{page_id}。"
                    )
                self._validated_page_revision(document)
                head_page_documents[page_id] = document
        if not isinstance(state_document, dict):
            raise CorruptProjectArtifactError(
                "项目绑定版本缺少有效的 Project State。"
            )
        if not isinstance(project_manifest, dict):
            raise CorruptProjectArtifactError(
                "项目绑定版本缺少有效的项目清单。"
            )
        return (
            normalized_project_id,
            head,
            state_document,
            project_manifest,
            head_page_documents,
        )

    def read_command_base(self, project_id: str, page_id: str) -> CommandBase:
        normalized_page_id = self.validated_page_id(page_id)
        (
            normalized_project_id,
            head,
            state_document,
            project_manifest,
            head_page_documents,
        ) = self._read_validated_command_documents(project_id)
        if head is None:
            # A legacy project has no authoritative Head file inventory, so a
            # page-scoped command validates only its explicitly referenced page.
            page_document = self.read_project_page_document(
                normalized_project_id,
                normalized_page_id,
            )
        else:
            assert head_page_documents is not None
            page_document = head_page_documents.get(normalized_page_id)
        if not isinstance(page_document, dict):
            raise CorruptProjectArtifactError(
                "项目绑定版本缺少有效的 Page Document。"
            )
        page_revision = self._validated_page_revision(page_document)
        return CommandBase(
            project_id=normalized_project_id,
            page_id=normalized_page_id,
            head=head,
            head_generation=int((head or {}).get("generation") or 0),
            head_revision_id=str((head or {}).get("revision_id") or ""),
            state_document=state_document,
            project_manifest=project_manifest,
            page_document=page_document,
            page_revision=page_revision,
        )

    def read_project_command_base(self, project_id: str) -> ProjectCommandBase:
        (
            normalized_project_id,
            head,
            state_document,
            project_manifest,
            head_page_documents,
        ) = self._read_validated_command_documents(project_id)
        page_documents: dict[str, dict[str, Any]] = {}
        source_images = state_document.get("source_images")
        if not isinstance(source_images, list):
            raise CorruptProjectArtifactError(
                "项目绑定版本缺少有效的页面列表。"
            )
        for image in source_images:
            if not isinstance(image, dict):
                raise CorruptProjectArtifactError(
                    "项目绑定版本页面列表已损坏。"
                )
            page_id = self.validated_page_id(
                str(image.get("stored_name") or "")
            )
            if head is None:
                # A legacy project has no authoritative Head file inventory.
                # Only its canonical source-image pages are discoverable here.
                document = self.read_project_page_document(
                    normalized_project_id,
                    page_id,
                )
            else:
                assert head_page_documents is not None
                document = head_page_documents.get(page_id)
            if not isinstance(document, dict):
                raise CorruptProjectArtifactError(
                    f"项目绑定版本缺少有效的 Page Document：{page_id}。"
                )
            if head is None:
                self._validated_page_revision(document)
            page_documents[page_id] = document
        return ProjectCommandBase(
            project_id=normalized_project_id,
            head=head,
            head_generation=int((head or {}).get("generation") or 0),
            head_revision_id=str((head or {}).get("revision_id") or ""),
            state_document=state_document,
            project_manifest=project_manifest,
            page_documents=page_documents,
        )

    def read_project_state_from_head(
        self,
        project_id: str,
        head: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_project_id = self.validated_project_id(project_id)
        state_document = self._read_bound_head_json(
            normalized_project_id,
            head,
            "state/session.json",
        )
        if not isinstance(state_document, dict):
            raise CorruptProjectArtifactError(
                "已提交的 Project Head 缺少有效的 Project State。"
            )
        return state_document

    def materialize_project_head_artifact(
        self,
        project_id: str,
        logical_path: str,
        destination: Path,
    ) -> Path:
        normalized_project_id = self.validated_project_id(project_id)
        self._validated_snapshot_logical_path(logical_path)
        head = self.read_project_head(normalized_project_id)
        if head is None:
            raise FileNotFoundError("项目还没有可读取的 Project Head。")
        metadata = dict(head.get("files") or {}).get(logical_path)
        if metadata is None:
            raise FileNotFoundError("Project Head 中不存在请求的产物。")
        raw_bytes = self._read_artifact_bytes(normalized_project_id, metadata)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".head-artifact",
            dir=str(destination.parent),
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, destination)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.remove(temporary_name)
        return destination

    @contextlib.contextmanager
    def materialize_page_working_set(
        self,
        base: CommandBase,
        *,
        legacy_project: dict[str, Any] | None = None,
    ) -> Iterator[PageWorkingSet]:
        project_dir = self.project_dir(base.project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        root = Path(
            tempfile.mkdtemp(
                prefix=".page-working-set-",
                dir=str(project_dir),
            )
        )
        source_dir = root / "source"
        translated_dir = root / "translated"
        cache_dir = root / "cache"
        def optional_path(field_name: str) -> Path | None:
            raw_path = str(base.state_document.get(field_name) or "").strip()
            return Path(raw_path) if raw_path else None

        canonical_source_dir = optional_path("source_dir")
        canonical_translated_dir = optional_path("translated_dir")
        canonical_cache_dir = optional_path("rerender_cache_dir")
        for path in (source_dir, translated_dir, cache_dir):
            path.mkdir(parents=True, exist_ok=True)
        try:
            if base.head is not None:
                self.restore_snapshot_artifacts(
                    base.project_id,
                    {
                        "schema_version": 1,
                        "files": dict(base.head.get("files") or {}),
                    },
                    {
                        "source": source_dir,
                        "translated": translated_dir,
                        "cache": cache_dir,
                    },
                )
            else:
                if not isinstance(legacy_project, dict):
                    raise CorruptProjectArtifactError(
                        "旧项目缺少可迁移的运行状态，无法创建 Working Set。"
                    )
                legacy_roots = {
                    "source": canonical_source_dir,
                    "translated": canonical_translated_dir,
                    "cache": canonical_cache_dir,
                }
                for name, legacy_root in legacy_roots.items():
                    destination = {
                        "source": source_dir,
                        "translated": translated_dir,
                        "cache": cache_dir,
                    }[name]
                    if legacy_root is not None and legacy_root.is_dir():
                        shutil.copytree(
                            legacy_root,
                            destination,
                            dirs_exist_ok=True,
                        )
            yield PageWorkingSet(
                base=base,
                root=root,
                source_dir=source_dir,
                translated_dir=translated_dir,
                cache_dir=cache_dir,
                canonical_source_dir=canonical_source_dir,
                canonical_translated_dir=canonical_translated_dir,
                canonical_cache_dir=canonical_cache_dir,
                legacy_runtime_session=(
                    dict(legacy_project) if base.head is None else None
                ),
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    @contextlib.contextmanager
    def materialize_project_working_set(
        self,
        base: ProjectCommandBase,
        *,
        action: str,
        resume_fingerprint: str,
        legacy_project: dict[str, Any] | None = None,
    ) -> Iterator[ProjectWorkingSet]:
        project_dir = self.project_dir(base.project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        root = Path(
            tempfile.mkdtemp(
                prefix=".project-working-set-",
                dir=str(project_dir),
            )
        )
        source_dir = root / "source"
        translated_dir = root / "translated"
        cache_dir = root / "cache"
        archive_dir = root / "archive"

        def optional_path(field_name: str) -> Path | None:
            raw_path = str(base.state_document.get(field_name) or "").strip()
            return Path(raw_path) if raw_path else None

        canonical_source_dir = optional_path("source_dir")
        canonical_translated_dir = optional_path("translated_dir")
        canonical_cache_dir = optional_path("rerender_cache_dir")
        canonical_archive_path = self.project_temp_path(
            base.project_id,
            "result.zip",
        )
        for path in (source_dir, translated_dir, cache_dir, archive_dir):
            path.mkdir(parents=True, exist_ok=True)
        initial_state_document = dict(base.state_document)
        page_checkpoints: Mapping[str, str] = MappingProxyType({})
        pending_restored = False
        try:
            if base.head is not None:
                self.restore_snapshot_artifacts(
                    base.project_id,
                    {
                        "schema_version": 1,
                        "files": dict(base.head.get("files") or {}),
                    },
                    {
                        "source": source_dir,
                        "translated": translated_dir,
                        "cache": cache_dir,
                        "archive": archive_dir,
                    },
                )
            else:
                if not isinstance(legacy_project, dict):
                    raise CorruptProjectArtifactError(
                        "旧项目缺少可迁移的运行状态，无法创建 Working Set。"
                    )
                for legacy_root, destination in (
                    (canonical_source_dir, source_dir),
                    (canonical_translated_dir, translated_dir),
                    (canonical_cache_dir, cache_dir),
                ):
                    if legacy_root is not None and legacy_root.is_dir():
                        shutil.copytree(
                            legacy_root,
                            destination,
                            dirs_exist_ok=True,
                        )
                if canonical_archive_path.is_file():
                    shutil.copy2(
                        canonical_archive_path,
                        archive_dir / "result.zip",
                    )

            # Invalid Pending data is diagnostic evidence and must fail closed.
            # A valid but non-matching set belongs to another command/base and
            # is deliberately not overlaid onto this Working Set.
            pending = self.read_pending_artifact_set(base.project_id)
            if pending is not None and (
                str(pending.get("action") or "") == str(action or "")
                and str(pending.get("resume_fingerprint") or "")
                == str(resume_fingerprint or "")
                and int(pending.get("base_head_generation") or 0)
                == base.head_generation
                and str(pending.get("base_head_revision_id") or "")
                == base.head_revision_id
            ):
                self.restore_pending_artifact_set(
                    base.project_id,
                    pending,
                    {
                        "translated": translated_dir,
                        "cache": cache_dir,
                        "archive": archive_dir,
                    },
                )
                initial_state_document = dict(pending["state_document"])
                page_checkpoints = MappingProxyType(
                    dict(pending.get("page_checkpoints") or {})
                )
                pending_restored = True

            yield ProjectWorkingSet(
                base=base,
                root=root,
                source_dir=source_dir,
                translated_dir=translated_dir,
                cache_dir=cache_dir,
                archive_dir=archive_dir,
                canonical_source_dir=canonical_source_dir,
                canonical_translated_dir=canonical_translated_dir,
                canonical_cache_dir=canonical_cache_dir,
                canonical_archive_path=canonical_archive_path,
                initial_state_document=initial_state_document,
                page_checkpoints=page_checkpoints,
                pending_restored=pending_restored,
                action=str(action or ""),
                resume_fingerprint=str(resume_fingerprint or ""),
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def commit_page_working_set(
        self,
        working_set: PageWorkingSet,
        prepared: PreparedHeadUpdate,
    ) -> ProjectHeadCommitResult:
        warnings: list[str] = []
        head = self.commit_project_head(
            working_set.base.project_id,
            state_document=prepared.state_document,
            project_manifest=prepared.project_manifest,
            page_documents=prepared.page_documents,
            artifact_files=prepared.artifact_files,
            expected_generation=working_set.base.head_generation,
            expected_revision_id=working_set.base.head_revision_id,
            replace_prefixes=prepared.replace_prefixes,
            remove_logical_paths=prepared.remove_logical_paths,
            warning_sink=warnings,
        )

        try:
            for logical_path in prepared.remove_logical_paths:
                parts = self._validated_snapshot_logical_path(logical_path)
                if (
                    parts[0] == "translated"
                    and working_set.canonical_translated_dir is not None
                ):
                    working_set.canonical_translated_dir.joinpath(*parts[1:]).unlink(
                        missing_ok=True
                    )
            if working_set.canonical_cache_dir is not None:
                target_cache_dir = (
                    working_set.canonical_cache_dir / working_set.base.page_id
                )
                shutil.rmtree(target_cache_dir, ignore_errors=True)
            destinations: dict[str, Path] = {}
            if working_set.canonical_translated_dir is not None:
                destinations["translated"] = working_set.canonical_translated_dir
            if working_set.canonical_cache_dir is not None:
                destinations["cache"] = working_set.canonical_cache_dir
            if destinations:
                self.restore_snapshot_artifacts(
                    working_set.base.project_id,
                    {"schema_version": 1, "files": dict(head.get("files") or {})},
                    destinations,
                )
        except Exception as exc:
            warnings.append(
                "Project Head committed but translated/cache compatibility "
                f"projection failed: {exc}"
            )
            logger.exception(
                "Project Head committed but artifact compatibility projection failed. "
                "project=%s page=%s",
                working_set.base.project_id,
                working_set.base.page_id,
            )

        try:
            self.refresh_project_index_entry(
                {
                    field: prepared.project_manifest[field]
                    for field in self.PROJECT_INDEX_FIELDS
                    if field in prepared.project_manifest
                }
            )
        except Exception as exc:
            warnings.append(
                f"Project Head committed but project index projection failed: {exc}"
            )
            logger.exception(
                "Project Head committed but project index projection failed. project=%s",
                working_set.base.project_id,
            )

        try:
            self.clear_obsolete_pending_artifact_set(
                working_set.base.project_id
            )
            self.garbage_collect_snapshot_blobs(
                working_set.base.project_id,
            )
        except Exception as exc:
            warnings.append(
                f"Project Head committed but post-commit cleanup failed: {exc}"
            )
            logger.exception(
                "Project Head committed but post-commit cleanup failed. project=%s",
                working_set.base.project_id,
            )

        return ProjectHeadCommitResult(
            head=head,
            warnings=tuple(warnings),
            runtime_session=dict(prepared.runtime_session),
        )

    def commit_project_working_set(
        self,
        working_set: ProjectWorkingSet,
        prepared: PreparedHeadUpdate,
    ) -> ProjectHeadCommitResult:
        warnings: list[str] = []
        head = self.commit_project_head(
            working_set.base.project_id,
            state_document=prepared.state_document,
            project_manifest=prepared.project_manifest,
            page_documents=prepared.page_documents,
            artifact_files=prepared.artifact_files,
            expected_generation=working_set.base.head_generation,
            expected_revision_id=working_set.base.head_revision_id,
            replace_prefixes=prepared.replace_prefixes,
            remove_logical_paths=prepared.remove_logical_paths,
            warning_sink=warnings,
        )

        try:
            previous_files = dict((working_set.base.head or {}).get("files") or {})
            next_files = dict(head.get("files") or {})
            roots = {
                "translated": working_set.canonical_translated_dir,
                "cache": working_set.canonical_cache_dir,
            }
            for logical_path in sorted(set(previous_files) - set(next_files)):
                parts = self._validated_snapshot_logical_path(logical_path)
                root = roots.get(parts[0])
                if root is not None:
                    root.joinpath(*parts[1:]).unlink(missing_ok=True)
            destinations = {
                prefix: root
                for prefix, root in roots.items()
                if root is not None
            }
            if destinations:
                self.restore_snapshot_artifacts(
                    working_set.base.project_id,
                    {"schema_version": 1, "files": next_files},
                    destinations,
                )
            if "archive/result.zip" in next_files:
                self.materialize_project_head_artifact(
                    working_set.base.project_id,
                    "archive/result.zip",
                    working_set.canonical_archive_path,
                )
            elif "archive/result.zip" in previous_files:
                working_set.canonical_archive_path.unlink(missing_ok=True)
        except Exception as exc:
            warnings.append(
                "Project Head committed but translated/cache compatibility "
                f"projection failed: {exc}"
            )
            logger.exception(
                "Project Head committed but project artifact compatibility "
                "projection failed. project=%s",
                working_set.base.project_id,
            )

        if prepared.snapshot_document is not None:
            try:
                self.create_project_head_snapshot(
                    working_set.base.project_id,
                    head,
                    prepared.snapshot_document,
                )
            except Exception as exc:
                warnings.append(
                    "Project Head committed but automatic snapshot/retention failed "
                    f"during creation: {exc}"
                )
                logger.exception(
                    "Project Head committed but automatic snapshot/retention failed "
                    "during creation. project=%s",
                    working_set.base.project_id,
                )

            try:
                self.enforce_snapshot_retention(working_set.base.project_id)
            except Exception as exc:
                warnings.append(
                    "Project Head committed but automatic snapshot/retention failed "
                    f"during retention: {exc}"
                )
                logger.exception(
                    "Project Head committed but automatic snapshot/retention failed "
                    "during retention. project=%s",
                    working_set.base.project_id,
                )

        snapshot_manifests: list[dict[str, Any]] | None = None
        try:
            snapshot_manifests = self.read_snapshot_manifests(
                working_set.base.project_id
            )
        except Exception as exc:
            warnings.append(
                f"Project Head committed but snapshot catalog projection failed: {exc}"
            )
            logger.exception(
                "Project Head committed but snapshot catalog projection failed. project=%s",
                working_set.base.project_id,
            )

        if snapshot_manifests is not None:
            try:
                project_summary = {
                    field: prepared.project_manifest[field]
                    for field in self.PROJECT_INDEX_FIELDS
                    if field in prepared.project_manifest
                }
                project_summary.update(
                    self._snapshot_index_summary(snapshot_manifests)
                )
                self.refresh_project_index_entry(project_summary)
            except Exception as exc:
                warnings.append(
                    f"Project Head committed but project index projection failed: {exc}"
                )
                logger.exception(
                    "Project Head committed but project index projection failed. project=%s",
                    working_set.base.project_id,
                )

        try:
            with self._head_commit_lock(working_set.base.project_id):
                pending = self.read_pending_artifact_set(
                    working_set.base.project_id
                )
                pending_owned_by_command = pending is not None and (
                    str(pending.get("action") or "") == working_set.action
                    and str(pending.get("resume_fingerprint") or "")
                    == working_set.resume_fingerprint
                    and int(pending.get("base_head_generation") or 0)
                    == working_set.base.head_generation
                    and str(pending.get("base_head_revision_id") or "")
                    == working_set.base.head_revision_id
                )
                if pending_owned_by_command:
                    self.clear_pending_artifact_set(
                        working_set.base.project_id
                    )
        except Exception as exc:
            warnings.append(
                f"Project Head committed but Pending cleanup failed: {exc}"
            )
            logger.exception(
                "Project Head committed but Pending cleanup failed. project=%s",
                working_set.base.project_id,
            )

        try:
            if snapshot_manifests is None:
                snapshot_manifests = self.read_snapshot_manifests(
                    working_set.base.project_id
                )
            self.garbage_collect_snapshot_blobs(
                working_set.base.project_id,
            )
        except Exception as exc:
            warnings.append(
                f"Project Head committed but artifact GC failed: {exc}"
            )
            logger.exception(
                "Project Head committed but artifact GC failed. project=%s",
                working_set.base.project_id,
            )

        return ProjectHeadCommitResult(
            head=head,
            warnings=tuple(warnings),
            runtime_session=dict(prepared.runtime_session),
        )

    def write_pending_artifact_set(
        self,
        project_id: str,
        *,
        action: str,
        resume_fingerprint: str,
        base_head: dict[str, Any] | None,
        state_document: dict[str, Any],
        files: dict[str, Path],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_project_id = self.validated_project_id(project_id)
        with self._head_commit_lock(normalized_project_id):
            return self._write_pending_artifact_set_locked(
                normalized_project_id,
                action=action,
                resume_fingerprint=resume_fingerprint,
                base_head=base_head,
                state_document=state_document,
                files=files,
                metadata=metadata,
            )

    def _write_pending_artifact_set_locked(
        self,
        project_id: str,
        *,
        action: str,
        resume_fingerprint: str,
        base_head: dict[str, Any] | None,
        state_document: dict[str, Any],
        files: dict[str, Path],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_project_id = project_id
        if (
            not isinstance(resume_fingerprint, str)
            or not resume_fingerprint
        ):
            raise CorruptProjectArtifactError(
                "待恢复产物指纹无效，拒绝写入。"
            )
        normalized_metadata = dict(metadata or {})
        raw_page_checkpoints = normalized_metadata.pop("page_checkpoints", {})
        if not isinstance(raw_page_checkpoints, dict):
            raise CorruptProjectArtifactError(
                "待恢复产物页面检查点无效，拒绝写入。"
            )
        try:
            page_checkpoints = {
                self.validated_page_id(page_id): stage
                for page_id, stage in raw_page_checkpoints.items()
            }
        except (InvalidStorageIdentifierError, TypeError) as exc:
            raise CorruptProjectArtifactError(
                "待恢复产物页面检查点无效，拒绝写入。"
            ) from exc
        for logical_path in files:
            self._validated_pending_logical_path(logical_path)
        provisional_pending = {
            **normalized_metadata,
            "schema_version": 2,
            "action": str(action or ""),
            "page_checkpoints": page_checkpoints,
        }
        try:
            pending_state = ProjectState.load(
                state_document,
                expected_project_id=normalized_project_id,
            )
            page_checkpoints = self._normalize_pending_page_checkpoints(
                provisional_pending,
                pending_state.workflow_stage,
            )
        except Exception as exc:
            raise CorruptProjectArtifactError(
                "待恢复产物内容无效，拒绝写入。"
            ) from exc
        previous_pending = self.read_pending_artifact_set(normalized_project_id)
        base_generation = int((base_head or {}).get("generation") or 0)
        base_revision_id = str((base_head or {}).get("revision_id") or "")
        exact_previous = previous_pending is not None and (
            str(previous_pending.get("action") or "") == str(action or "")
            and str(previous_pending.get("resume_fingerprint") or "")
            == str(resume_fingerprint or "")
            and int(previous_pending.get("base_head_generation") or 0)
            == base_generation
            and str(previous_pending.get("base_head_revision_id") or "")
            == base_revision_id
        )
        if exact_previous:
            stage_rank = {"detected": 1, "rendered": 2, "finalized": 3}
            previous_checkpoints = dict(
                previous_pending.get("page_checkpoints") or {}
            )
            if not previous_checkpoints.keys() <= page_checkpoints.keys() or any(
                stage_rank.get(page_checkpoints.get(page_id), 0)
                < stage_rank.get(previous_stage, 0)
                for page_id, previous_stage in previous_checkpoints.items()
            ):
                raise CorruptProjectArtifactError(
                    "待恢复产物页面进度不可回退或删除，拒绝写入。"
                )
        previous_bundle = (
            previous_pending.get("artifact_bundle")
            if isinstance(previous_pending, dict)
            else None
        )
        artifact_bundle = self.capture_snapshot_artifacts(
            normalized_project_id,
            files,
            previous_bundle=previous_bundle,
        )
        pending = {
            **normalized_metadata,
            "schema_version": 2,
            "pending_id": uuid.uuid4().hex,
            "project_id": normalized_project_id,
            "action": str(action or ""),
            "resume_fingerprint": str(resume_fingerprint or ""),
            "base_head_generation": base_generation,
            "base_head_revision_id": base_revision_id,
            "page_checkpoints": page_checkpoints,
            "state_document": state_document,
            "artifact_bundle": artifact_bundle,
        }
        try:
            self._validate_pending_checkpoint_claims(
                normalized_project_id,
                page_checkpoints,
                pending_state,
                artifact_bundle["files"],
            )
        except Exception as exc:
            raise CorruptProjectArtifactError(
                "待恢复产物内容无效，拒绝写入。"
            ) from exc
        self.write_json_file(
            self.project_pending_artifact_path(normalized_project_id),
            pending,
        )
        return pending

    def restore_pending_artifact_set(
        self,
        project_id: str,
        pending: dict[str, Any],
        destinations: dict[str, Path],
    ) -> set[str]:
        pending_schema = pending.get("schema_version") if isinstance(pending, dict) else None
        if (
            not isinstance(pending, dict)
            or isinstance(pending_schema, bool)
            or pending_schema not in {1, 2}
        ):
            raise CorruptProjectArtifactError("待恢复产物清单格式无效。")
        artifact_bundle = pending.get("artifact_bundle")
        raw_files = (
            artifact_bundle.get("files")
            if isinstance(artifact_bundle, dict)
            else None
        )
        if not isinstance(raw_files, dict):
            raise CorruptProjectArtifactError("待恢复产物引用已损坏，无法安全恢复。")
        for logical_path in raw_files:
            self._validated_pending_logical_path(logical_path)
        return self.restore_snapshot_artifacts(
            project_id,
            artifact_bundle,
            destinations,
        )

    def clear_pending_artifact_set(self, project_id: str) -> None:
        normalized_project_id = self.validated_project_id(project_id)
        with self._head_commit_lock(normalized_project_id):
            self._clear_pending_artifact_set_locked(normalized_project_id)

    def _clear_pending_artifact_set_locked(self, project_id: str) -> None:
        self.project_pending_artifact_path(project_id).unlink(missing_ok=True)

    def clear_obsolete_pending_artifact_set(self, project_id: str) -> bool:
        normalized_project_id = self.validated_project_id(project_id)
        with self._head_commit_lock(normalized_project_id):
            current_head = self.read_project_head(normalized_project_id)
            pending = self.read_pending_artifact_set(normalized_project_id)
            if pending is None:
                return False
            current_revision_id = (
                current_head.get("revision_id") if current_head is not None else ""
            )
            if pending["base_head_revision_id"] == current_revision_id:
                return False
            self._clear_pending_artifact_set_locked(normalized_project_id)
            return True

    def commit_project_head(
        self,
        project_id: str,
        *,
        state_document: dict[str, Any],
        project_manifest: dict[str, Any],
        page_documents: dict[str, dict[str, Any]],
        artifact_files: dict[str, Path] | None = None,
        expected_generation: int | None = None,
        expected_revision_id: str | None = None,
        replace_prefixes: tuple[str, ...] = (),
        remove_logical_paths: set[str] | None = None,
        warning_sink: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_project_id = self.validated_project_id(project_id)
        with self._head_commit_lock(normalized_project_id):
            return self._commit_project_head_locked(
                normalized_project_id,
                state_document=state_document,
                project_manifest=project_manifest,
                page_documents=page_documents,
                artifact_files=artifact_files,
                expected_generation=expected_generation,
                expected_revision_id=expected_revision_id,
                replace_prefixes=replace_prefixes,
                remove_logical_paths=remove_logical_paths,
                warning_sink=warning_sink,
            )

    def _commit_project_head_locked(
        self,
        project_id: str,
        *,
        state_document: dict[str, Any],
        project_manifest: dict[str, Any],
        page_documents: dict[str, dict[str, Any]],
        artifact_files: dict[str, Path] | None = None,
        expected_generation: int | None = None,
        expected_revision_id: str | None = None,
        replace_prefixes: tuple[str, ...] = (),
        remove_logical_paths: set[str] | None = None,
        warning_sink: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_project_id = project_id
        current_head = self.read_project_head(normalized_project_id)
        current_generation = int((current_head or {}).get("generation") or 0)
        if (
            expected_generation is not None
            and int(expected_generation) != current_generation
        ):
            raise ProjectHeadConflictError(
                expected_generation=int(expected_generation),
                actual_generation=current_generation,
                expected_revision_id=str(expected_revision_id or ""),
                actual_revision_id=str((current_head or {}).get("revision_id") or ""),
            )
        current_revision_id = str((current_head or {}).get("revision_id") or "")
        if (
            expected_revision_id is not None
            and str(expected_revision_id) != current_revision_id
        ):
            raise ProjectHeadConflictError(
                expected_generation=int(expected_generation or 0),
                actual_generation=current_generation,
                expected_revision_id=str(expected_revision_id),
                actual_revision_id=current_revision_id,
            )
        normalized_replace_prefixes: list[str] = []
        for raw_prefix in replace_prefixes:
            normalized_prefix = str(raw_prefix or "").strip().replace("\\", "/")
            normalized_prefix = normalized_prefix.rstrip("/") + "/"
            self._validated_snapshot_logical_path(f"{normalized_prefix}placeholder")
            normalized_replace_prefixes.append(normalized_prefix)
        normalized_remove_paths = {
            str(logical_path or "").strip().replace("\\", "/")
            for logical_path in (remove_logical_paths or set())
        }
        for logical_path in normalized_remove_paths:
            self._validated_snapshot_logical_path(logical_path)
        current_files = {
            logical_path: metadata
            for logical_path, metadata in dict(
                (current_head or {}).get("files") or {}
            ).items()
            if logical_path not in normalized_remove_paths
            and not any(
                logical_path.startswith(prefix)
                for prefix in normalized_replace_prefixes
            )
        }
        project_dir = self.project_dir(normalized_project_id)
        project_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(
            prefix=".artifact-head-",
            dir=str(project_dir),
        ) as temporary_dir_name:
            temporary_dir = Path(temporary_dir_name)
            staged_files: dict[str, Path] = {}

            def stage_json(logical_path: str, payload: dict[str, Any]) -> None:
                parts = self._validated_snapshot_logical_path(logical_path)
                staged_path = temporary_dir.joinpath(*parts)
                self.write_json_file(staged_path, payload)
                staged_files[logical_path] = staged_path

            stage_json("state/session.json", state_document)
            stage_json("project/project.json", project_manifest)
            normalized_page_documents: dict[str, dict[str, Any]] = {}
            for page_id, document in sorted(page_documents.items()):
                normalized_page_id = self.validated_page_id(page_id)
                normalized_page_documents[normalized_page_id] = dict(document)
                stage_json(
                    f"pages/{normalized_page_id}/page_document.json",
                    dict(document),
                )
            for logical_path, source_path in sorted((artifact_files or {}).items()):
                self._validated_snapshot_logical_path(logical_path)
                # Structured documents above are the new revision being
                # committed. A legacy compatibility projection may still
                # exist at the same logical path, but it must never overwrite
                # the freshly staged document in Project Head.
                staged_files.setdefault(logical_path, Path(source_path))

            captured = self.capture_snapshot_artifacts(
                normalized_project_id,
                staged_files,
                previous_bundle=current_head,
            )

        next_files = {**current_files, **captured["files"]}
        generation = current_generation + 1
        revision_id = f"g{generation:08d}-{uuid.uuid4().hex[:12]}"
        next_head = {
            "schema_version": 1,
            "project_id": normalized_project_id,
            "generation": generation,
            "revision_id": revision_id,
            "files": next_files,
        }
        with self._head_commit_lock(normalized_project_id):
            head_before_swap = self.read_project_head(normalized_project_id)
            actual_generation = int((head_before_swap or {}).get("generation") or 0)
            actual_revision_id = str(
                (head_before_swap or {}).get("revision_id") or ""
            )
            if (
                expected_generation is not None
                and int(expected_generation) != actual_generation
            ) or (
                expected_revision_id is not None
                and str(expected_revision_id) != actual_revision_id
            ):
                raise ProjectHeadConflictError(
                    expected_generation=int(expected_generation or 0),
                    actual_generation=actual_generation,
                    expected_revision_id=str(expected_revision_id or ""),
                    actual_revision_id=actual_revision_id,
                )
            revisions_dir = self.project_revisions_dir(normalized_project_id)
            revisions_dir.mkdir(parents=True, exist_ok=True)
            self.write_json_file(revisions_dir / f"{revision_id}.json", next_head)
            self.write_json_file(self.project_head_path(normalized_project_id), next_head)

        # These files remain compatibility projections for code that has not yet
        # migrated to Project Head reads. The atomic head pointer is authoritative.
        compatibility_projections = {
            self.project_session_state_path(normalized_project_id): state_document,
            self.project_manifest_path(normalized_project_id): project_manifest,
            **{
                self.project_page_document_path(normalized_project_id, page_id): document
                for page_id, document in normalized_page_documents.items()
            },
        }
        for projection_path, projection_payload in compatibility_projections.items():
            try:
                self.write_json_file(projection_path, projection_payload)
            except Exception:
                logical_projection = (
                    "state/session.json"
                    if projection_path
                    == self.project_session_state_path(normalized_project_id)
                    else "project/project.json"
                    if projection_path
                    == self.project_manifest_path(normalized_project_id)
                    else str(projection_path.relative_to(project_dir))
                )
                if warning_sink is not None:
                    warning_sink.append(
                        "Project Head committed but compatibility projection "
                        f"failed: {logical_projection}"
                    )
                logger.exception(
                    "Project Head committed but a compatibility projection could not be refreshed. "
                    "project=%s generation=%s path=%s",
                    normalized_project_id,
                    generation,
                    projection_path,
                )
        return next_head

    def _read_project_head_json(
        self,
        project_id: str,
        logical_path: str,
    ) -> Any | None:
        head = self.read_project_head(project_id)
        if head is None:
            return None
        metadata = head["files"].get(logical_path)
        if metadata is None:
            return None
        raw_bytes = self._read_artifact_bytes(project_id, metadata)
        try:
            return json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise CorruptProjectArtifactError(
                f"项目当前版本中的 {logical_path} 已损坏，无法安全读取。"
            ) from exc

    def _read_artifact_bytes(
        self,
        project_id: str,
        metadata: Any,
    ) -> bytes:
        if not isinstance(metadata, dict):
            raise CorruptProjectArtifactError("项目产物引用格式无效，无法安全读取。")
        blob_id = str(metadata.get("blob") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", blob_id):
            raise CorruptProjectArtifactError("项目产物摘要无效，无法安全读取。")
        blob_path = self.project_artifact_store_dir(project_id) / blob_id[:2] / blob_id
        if not blob_path.is_file():
            raise CorruptProjectArtifactError("项目产物文件缺失，无法安全读取。")
        raw_bytes = blob_path.read_bytes()
        if hashlib.sha256(raw_bytes).hexdigest() != blob_id:
            raise CorruptProjectArtifactError("项目产物校验失败，无法安全读取。")
        return raw_bytes

    def page_document_region_count(self, project_id: str, page_id: str) -> int:
        try:
            payload = self.read_project_page_document(project_id, page_id)
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

        candidates: list[tuple[Path, dict[str, Any], str, str]] = []
        for path in sorted(snapshots_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise CorruptProjectArtifactError(
                    "项目快照清单已损坏，无法安全读取。"
                ) from exc
            if not isinstance(payload, dict):
                raise CorruptProjectArtifactError(
                    "项目快照清单必须是 JSON 对象。"
                )
            raw_snapshot_id = payload.get("snapshot_id")
            if (
                not isinstance(raw_snapshot_id, str)
                or not raw_snapshot_id
                or raw_snapshot_id != raw_snapshot_id.strip()
            ):
                raise CorruptProjectArtifactError("项目快照清单缺少有效标识。")
            snapshot_id = raw_snapshot_id
            try:
                snapshot_id = self.validated_page_id(snapshot_id)
            except InvalidStorageIdentifierError as exc:
                raise CorruptProjectArtifactError(
                    "项目快照清单标识不是安全的文件名。"
                ) from exc
            created_at = payload.get("created_at")
            if not isinstance(created_at, str) or not created_at.strip():
                raise CorruptProjectArtifactError("项目快照清单缺少创建时间。")
            candidates.append((path, payload, snapshot_id, created_at.strip()))

        snapshot_ids = [snapshot_id for _, _, snapshot_id, _ in candidates]
        if len(set(snapshot_ids)) != len(snapshot_ids):
            raise CorruptProjectArtifactError("项目快照清单包含重复标识。")

        for path, payload, snapshot_id, created_at in candidates:
            if snapshot_id != path.stem:
                raise CorruptProjectArtifactError(
                    "项目快照清单标识与文件名不一致。"
                )
            manifest = dict(payload)
            manifest["snapshot_id"] = snapshot_id
            manifest["created_at"] = created_at
            manifest["_path"] = str(path)
            manifests.append(manifest)

        manifests.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return manifests

    @staticmethod
    def _snapshot_index_summary(
        manifests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        latest = manifests[0] if manifests else {}
        return {
            "latest_snapshot_id": str(latest.get("snapshot_id") or ""),
            "latest_snapshot_kind": str(latest.get("kind") or ""),
            "latest_snapshot_summary": str(latest.get("summary") or ""),
            "snapshot_count": len(manifests),
        }

    def create_project_head_snapshot(
        self,
        project_id: str,
        head: dict[str, Any],
        snapshot_document: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_project_id = self.validated_project_id(project_id)
        with self._head_commit_lock(normalized_project_id):
            return self._create_project_head_snapshot_locked(
                normalized_project_id,
                head,
                snapshot_document,
            )

    def _create_project_head_snapshot_locked(
        self,
        project_id: str,
        head: dict[str, Any],
        snapshot_document: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_project_id = project_id
        if str(head.get("project_id") or "") != normalized_project_id:
            raise CorruptProjectArtifactError(
                "自动快照的 Project Head 所属项目无效。"
            )
        generation = head.get("generation")
        raw_revision_id = head.get("revision_id")
        if (
            not isinstance(raw_revision_id, str)
            or not raw_revision_id
            or raw_revision_id != raw_revision_id.strip()
        ):
            raise CorruptProjectArtifactError(
                "自动快照缺少有效的 Project Head 引用。"
            )
        try:
            revision_id = self.validated_page_id(raw_revision_id)
        except InvalidStorageIdentifierError as exc:
            raise CorruptProjectArtifactError(
                "自动快照的 Project Head 版本标识不是安全的文件名。"
            ) from exc
        files = head.get("files")
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
            or not revision_id
            or not isinstance(files, dict)
        ):
            raise CorruptProjectArtifactError("自动快照缺少有效的 Project Head 引用。")
        revision_path = self.safe_storage_child(
            self.project_revisions_dir(normalized_project_id),
            f"{revision_id}.json",
            label="Project Head 版本标识",
        )
        try:
            stored_revision = json.loads(revision_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CorruptProjectArtifactError(
                "自动快照引用的 Project Head 版本不存在或已损坏。"
            ) from exc
        if not isinstance(stored_revision, dict):
            raise CorruptProjectArtifactError(
                "自动快照引用的 Project Head 版本必须是 JSON 对象。"
            )
        for field in (
            "schema_version",
            "project_id",
            "generation",
            "revision_id",
            "files",
        ):
            supplied_value = head.get(field)
            stored_value = stored_revision.get(field)
            if not self._strict_json_equal(stored_value, supplied_value):
                raise CorruptProjectArtifactError(
                    "自动快照的 Project Head 与持久版本不一致。"
                )
        self._validate_project_head_file_evidence(
            normalized_project_id,
            stored_revision["files"],
        )
        self._validate_project_head_file_evidence(
            normalized_project_id,
            files,
        )
        kind = str(snapshot_document.get("kind") or "").strip()
        if not kind:
            raise CorruptProjectArtifactError("自动快照缺少类型。")
        raw_created_at = snapshot_document.get("created_at")
        if raw_created_at is None:
            created_at = datetime.now(timezone.utc).isoformat()
        elif (
            not isinstance(raw_created_at, str)
            or not raw_created_at
            or raw_created_at != raw_created_at.strip()
        ):
            raise CorruptProjectArtifactError(
                "自动快照的创建时间无法生成安全标识。"
            )
        else:
            created_at = raw_created_at
        raw_snapshot_id = (
            f"{created_at.replace(':', '').replace('-', '')}_"
            f"{uuid.uuid4().hex[:8]}"
        )
        if raw_snapshot_id != raw_snapshot_id.strip():
            raise CorruptProjectArtifactError("自动快照标识不是安全的文件名。")
        try:
            snapshot_id = self.validated_page_id(raw_snapshot_id)
        except InvalidStorageIdentifierError as exc:
            raise CorruptProjectArtifactError(
                "自动快照标识不是安全的文件名。"
            ) from exc
        snapshot = {
            **dict(snapshot_document),
            "snapshot_id": snapshot_id,
            "project_id": normalized_project_id,
            "created_at": created_at,
            "pinned": False,
            "artifact_bundle": {
                "schema_version": 1,
                "files": dict(files),
            },
            "project_head_generation": generation,
            "project_head_revision_id": revision_id,
        }
        snapshots_dir = self.project_snapshots_dir(normalized_project_id)
        snapshot_path = self.safe_storage_child(
            snapshots_dir,
            f"{snapshot_id}.json",
            label="自动快照标识",
        )
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.write_json_file(snapshot_path, snapshot)
        return snapshot

    def enforce_snapshot_retention(self, project_id: str) -> None:
        normalized_project_id = self.validated_project_id(project_id)
        with self._head_commit_lock(normalized_project_id):
            self._enforce_snapshot_retention_locked(normalized_project_id)

    def set_snapshot_pinned(
        self,
        project_id: str,
        snapshot_id: str,
        pinned: bool,
    ) -> list[dict[str, Any]]:
        """Atomically update a pin, apply retention, and return the latest catalog."""
        normalized_project_id = self.validated_project_id(project_id)
        with self._head_commit_lock(normalized_project_id):
            manifests = self.read_snapshot_manifests(normalized_project_id)
            target_snapshot = next(
                (
                    item
                    for item in manifests
                    if item.get("snapshot_id") == snapshot_id
                ),
                None,
            )
            if target_snapshot is None:
                raise FileNotFoundError("目标快照不存在，请刷新后重试。")

            if pinned:
                pinned_count = sum(
                    1 for item in manifests if bool(item.get("pinned"))
                )
                if not bool(target_snapshot.get("pinned")) and pinned_count >= 10:
                    raise ValueError("固定快照最多保留 10 个，请先取消固定旧快照。")

            payload = {
                key: value
                for key, value in target_snapshot.items()
                if key != "_path"
            }
            payload["pinned"] = bool(pinned)
            self.write_json_file(Path(target_snapshot["_path"]), payload)
            self._enforce_snapshot_retention_locked(normalized_project_id)
            return self.read_snapshot_manifests(normalized_project_id)

    def _enforce_snapshot_retention_locked(self, project_id: str) -> None:
        manifests = self.read_snapshot_manifests(project_id)
        auto_snapshots = [item for item in manifests if not bool(item.get("pinned"))]
        victims: list[dict[str, Any]] = []
        while len(manifests) - len(victims) > 30:
            candidate = next(
                (item for item in reversed(auto_snapshots) if item not in victims),
                None,
            )
            if candidate is None:
                break
            victims.append(candidate)
        while (
            len(auto_snapshots)
            - sum(1 for item in victims if item in auto_snapshots)
            > 20
        ):
            candidate = next(
                (item for item in reversed(auto_snapshots) if item not in victims),
                None,
            )
            if candidate is None:
                break
            victims.append(candidate)
        for victim in victims:
            victim_path = Path(str(victim.get("_path") or ""))
            if victim_path.is_file():
                victim_path.unlink()

    @staticmethod
    def _validated_snapshot_logical_path(logical_path: str) -> tuple[str, ...]:
        normalized = str(logical_path or "").strip().replace("\\", "/")
        parsed = PurePosixPath(normalized)
        if (
            not normalized
            or parsed.is_absolute()
            or not parsed.parts
            or any(part in {"", ".", ".."} for part in parsed.parts)
        ):
            raise CorruptSnapshotArtifactError("快照产物路径无效，无法安全恢复。")
        return parsed.parts

    @staticmethod
    def _validated_project_head_logical_path(logical_path: Any) -> tuple[str, ...]:
        if (
            not isinstance(logical_path, str)
            or not logical_path
            or logical_path != logical_path.strip()
            or "\\" in logical_path
            or "\x00" in logical_path
        ):
            raise CorruptProjectArtifactError(
                "自动快照的 Project Head 包含无效产物路径。"
            )
        parsed = PurePosixPath(logical_path)
        if (
            parsed.is_absolute()
            or not parsed.parts
            or any(part in {"", ".", ".."} for part in parsed.parts)
            or parsed.as_posix() != logical_path
        ):
            raise CorruptProjectArtifactError(
                "自动快照的 Project Head 包含无效产物路径。"
            )
        return parsed.parts

    @classmethod
    def _strict_json_equal(cls, left: Any, right: Any) -> bool:
        if type(left) is not type(right):
            return False
        if isinstance(left, dict):
            return left.keys() == right.keys() and all(
                cls._strict_json_equal(left[key], right[key])
                for key in left
            )
        if isinstance(left, list):
            return len(left) == len(right) and all(
                cls._strict_json_equal(left_item, right_item)
                for left_item, right_item in zip(left, right)
            )
        return left == right

    def _validate_project_head_file_evidence(
        self,
        project_id: str,
        files: dict[str, Any],
    ) -> None:
        for logical_path, metadata in files.items():
            self._validated_project_head_logical_path(logical_path)
            if not isinstance(metadata, dict):
                raise CorruptProjectArtifactError(
                    "自动快照的 Project Head 产物记录已损坏。"
                )
            blob_id = metadata.get("blob")
            size = metadata.get("size")
            if (
                not isinstance(blob_id, str)
                or re.fullmatch(r"[0-9a-f]{64}", blob_id) is None
            ):
                raise CorruptProjectArtifactError(
                    "自动快照的 Project Head 产物摘要无效。"
                )
            if (
                isinstance(size, bool)
                or not isinstance(size, int)
                or size < 0
            ):
                raise CorruptProjectArtifactError(
                    "自动快照的 Project Head 产物大小无效。"
                )
            for timestamp_field in ("mtime_ns", "ctime_ns"):
                timestamp = metadata.get(timestamp_field)
                if timestamp is not None and (
                    isinstance(timestamp, bool)
                    or not isinstance(timestamp, int)
                    or timestamp < 0
                ):
                    raise CorruptProjectArtifactError(
                        "自动快照的 Project Head 产物元数据无效。"
                    )
            try:
                raw_bytes = self._read_artifact_bytes(project_id, metadata)
            except CorruptProjectArtifactError:
                raise
            except OSError as exc:
                raise CorruptProjectArtifactError(
                    "自动快照的 Project Head 产物无法安全读取。"
                ) from exc
            if len(raw_bytes) != size:
                raise CorruptProjectArtifactError(
                    "自动快照的 Project Head 产物大小不一致。"
                )

    @classmethod
    def _validated_pending_logical_path(cls, logical_path: str) -> tuple[str, ...]:
        parts = cls._validated_snapshot_logical_path(logical_path)
        if len(parts) < 2 or parts[0] not in {"translated", "cache", "archive"}:
            raise CorruptProjectArtifactError(
                "待恢复产物路径不属于受支持的 Working Set，无法安全恢复。"
            )
        return parts

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def capture_snapshot_artifacts(
        self,
        project_id: str,
        files: dict[str, Path],
        previous_bundle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        blobs_dir = self.project_artifact_store_dir(project_id)
        staging_dir = blobs_dir / ".staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        captured: dict[str, dict[str, Any]] = {}
        previous_files = (
            previous_bundle.get("files")
            if isinstance(previous_bundle, dict) and previous_bundle.get("schema_version") == 1
            else {}
        )
        if not isinstance(previous_files, dict):
            previous_files = {}

        for logical_path, source_path in sorted(files.items()):
            self._validated_snapshot_logical_path(logical_path)
            source = Path(source_path)
            if not source.is_file() or source.is_symlink():
                continue

            source_stat = source.stat()
            previous = previous_files.get(logical_path)
            previous_blob_id = str(previous.get("blob") or "").strip().lower() if isinstance(previous, dict) else ""
            previous_blob_path = blobs_dir / previous_blob_id[:2] / previous_blob_id
            if (
                re.fullmatch(r"[0-9a-f]{64}", previous_blob_id)
                and previous_blob_path.is_file()
                and int(previous.get("size") or -1) == source_stat.st_size
                and int(previous.get("mtime_ns") or -1) == source_stat.st_mtime_ns
                and int(previous.get("ctime_ns") or -1) == source_stat.st_ctime_ns
            ):
                captured[logical_path] = {
                    "blob": previous_blob_id,
                    "size": source_stat.st_size,
                    "mtime_ns": source_stat.st_mtime_ns,
                    "ctime_ns": source_stat.st_ctime_ns,
                }
                continue

            blob_id = self._sha256_file(source)
            size = source_stat.st_size
            blob_path = blobs_dir / blob_id[:2] / blob_id
            if not blob_path.exists():
                blob_path.parent.mkdir(parents=True, exist_ok=True)
                fd, temp_name = tempfile.mkstemp(prefix="blob_", suffix=".tmp", dir=str(staging_dir))
                os.close(fd)
                try:
                    shutil.copy2(source, temp_name)
                    if self._sha256_file(Path(temp_name)) != blob_id:
                        raise CorruptSnapshotArtifactError("快照产物在保存过程中发生变化，请重试。")
                    os.replace(temp_name, blob_path)
                finally:
                    with contextlib.suppress(FileNotFoundError):
                        os.remove(temp_name)
            captured[logical_path] = {
                "blob": blob_id,
                "size": size,
                "mtime_ns": source_stat.st_mtime_ns,
                "ctime_ns": source_stat.st_ctime_ns,
            }

        with contextlib.suppress(OSError):
            staging_dir.rmdir()
        return {
            "schema_version": 1,
            "files": captured,
        }

    def restore_snapshot_artifacts(
        self,
        project_id: str,
        bundle: dict[str, Any],
        destinations: dict[str, Path],
    ) -> set[str]:
        if not isinstance(bundle, dict) or bundle.get("schema_version") != 1:
            raise CorruptSnapshotArtifactError("快照产物版本不受支持，无法安全恢复。")
        raw_files = bundle.get("files")
        if not isinstance(raw_files, dict):
            raise CorruptSnapshotArtifactError("快照产物清单已损坏，无法安全恢复。")

        blobs_dir = self.project_artifact_store_dir(project_id)
        verified_blobs: set[str] = set()
        restored_roots: set[str] = set()
        for logical_path, metadata in sorted(raw_files.items()):
            parts = self._validated_snapshot_logical_path(logical_path)
            if not isinstance(metadata, dict):
                raise CorruptSnapshotArtifactError("快照产物记录已损坏，无法安全恢复。")
            blob_id = str(metadata.get("blob") or "").strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", blob_id):
                raise CorruptSnapshotArtifactError("快照产物摘要无效，无法安全恢复。")
            destination_root = destinations.get(parts[0])
            if destination_root is None:
                continue
            destination_root = Path(destination_root)
            destination = destination_root.joinpath(*parts[1:])
            resolved_root = destination_root.resolve()
            resolved_destination = destination.resolve()
            if resolved_destination == resolved_root or resolved_root not in resolved_destination.parents:
                raise CorruptSnapshotArtifactError("快照恢复路径越界，已停止恢复。")

            blob_path = blobs_dir / blob_id[:2] / blob_id
            if not blob_path.is_file():
                raise CorruptSnapshotArtifactError("快照产物缺失，无法完整恢复该历史版本。")
            if blob_id not in verified_blobs:
                if self._sha256_file(blob_path) != blob_id:
                    raise CorruptSnapshotArtifactError("快照产物校验失败，无法安全恢复。")
                verified_blobs.add(blob_id)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(blob_path, destination)
            restored_roots.add(parts[0])
        return restored_roots

    def garbage_collect_snapshot_blobs(
        self,
        project_id: str,
    ) -> None:
        normalized_project_id = self.validated_project_id(project_id)
        with self._head_commit_lock(normalized_project_id):
            self._garbage_collect_snapshot_blobs_locked(
                normalized_project_id,
            )

    def _garbage_collect_snapshot_blobs_locked(
        self,
        project_id: str,
    ) -> None:
        manifests = self.read_snapshot_manifests(project_id)
        referenced: set[str] = set()
        referenced_revisions: set[str] = set()
        current_head = self.read_project_head(project_id)
        current_revision_id = str((current_head or {}).get("revision_id") or "").strip()
        if current_revision_id:
            referenced_revisions.add(current_revision_id)
        head_files = (current_head or {}).get("files") or {}
        if isinstance(head_files, dict):
            for metadata in head_files.values():
                blob_id = str(metadata.get("blob") or "").strip().lower() if isinstance(metadata, dict) else ""
                if re.fullmatch(r"[0-9a-f]{64}", blob_id):
                    referenced.add(blob_id)

        pending = self.read_pending_artifact_set(project_id)
        if pending is not None:
            pending_revision_id = str(
                pending.get("base_head_revision_id") or ""
            ).strip()
            if pending_revision_id:
                referenced_revisions.add(pending_revision_id)
            pending_bundle = pending.get("artifact_bundle")
            pending_files = (
                pending_bundle.get("files")
                if isinstance(pending_bundle, dict)
                else None
            )
            if isinstance(pending_files, dict):
                for metadata in pending_files.values():
                    blob_id = str(metadata.get("blob") or "").strip().lower() if isinstance(metadata, dict) else ""
                    if re.fullmatch(r"[0-9a-f]{64}", blob_id):
                        referenced.add(blob_id)
        for manifest in manifests:
            snapshot_revision_id = str(
                manifest.get("project_head_revision_id") or ""
            ).strip() if isinstance(manifest, dict) else ""
            if snapshot_revision_id:
                referenced_revisions.add(snapshot_revision_id)
            bundle = manifest.get("artifact_bundle") if isinstance(manifest, dict) else None
            files = bundle.get("files") if isinstance(bundle, dict) else None
            if not isinstance(files, dict):
                continue
            for metadata in files.values():
                blob_id = str(metadata.get("blob") or "").strip().lower() if isinstance(metadata, dict) else ""
                if re.fullmatch(r"[0-9a-f]{64}", blob_id):
                    referenced.add(blob_id)

        revisions_dir = self.project_revisions_dir(project_id)
        if revisions_dir.exists():
            for revision_path in revisions_dir.glob("*.json"):
                if revision_path.stem not in referenced_revisions:
                    with contextlib.suppress(OSError):
                        revision_path.unlink()
            with contextlib.suppress(OSError):
                revisions_dir.rmdir()

        blobs_dir = self.project_artifact_store_dir(project_id)
        if not blobs_dir.exists():
            return
        for blob_path in blobs_dir.glob("[0-9a-f][0-9a-f]/*"):
            if blob_path.is_file() and blob_path.name not in referenced:
                with contextlib.suppress(OSError):
                    blob_path.unlink()
        for directory in sorted(blobs_dir.iterdir(), reverse=True):
            if directory.is_dir():
                with contextlib.suppress(OSError):
                    directory.rmdir()
        with contextlib.suppress(OSError):
            blobs_dir.rmdir()

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

    def rebuild_project_index(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        project_dirs = (
            sorted(path for path in self.projects_root.iterdir() if path.is_dir())
            if self.projects_root.exists()
            else []
        )
        for project_dir in project_dirs:
            try:
                project_id = self.validated_project_id(project_dir.name)
            except InvalidStorageIdentifierError:
                continue
            payload = self.read_project_manifest(project_id)
            if not isinstance(payload, dict) or not payload:
                continue
            manifest_project_id = str(payload.get("project_id") or "").strip()
            if manifest_project_id != project_id:
                continue
            project_summary = {
                    field: payload[field]
                    for field in self.PROJECT_INDEX_FIELDS
                    if field in payload
            }
            project_summary.update(
                self._snapshot_index_summary(
                    self.read_snapshot_manifests(project_id)
                )
            )
            summaries.append(project_summary)
        self.write_project_index(summaries)
        return summaries
