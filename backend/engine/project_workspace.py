from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from domain.project_state import CorruptProjectStateError, ProjectStateError
from runtime_paths import AppPaths


logger = logging.getLogger(__name__)


class InvalidStorageIdentifierError(ValueError):
    pass


class CorruptSnapshotArtifactError(ProjectStateError):
    pass


class CorruptProjectArtifactError(ProjectStateError):
    pass


class ProjectHeadConflictError(ProjectStateError):
    def __init__(self, *, expected_generation: int, actual_generation: int):
        self.expected_generation = expected_generation
        self.actual_generation = actual_generation
        super().__init__(
            "项目当前版本已变化，请刷新后重试。"
        )


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
        path = self.project_pending_artifact_path(project_id)
        if not path.exists():
            return None
        payload = self.read_json_file(path, None)
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
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
        return payload

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
        previous_pending = self.read_pending_artifact_set(normalized_project_id)
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
            **dict(metadata or {}),
            "schema_version": 1,
            "pending_id": uuid.uuid4().hex,
            "project_id": normalized_project_id,
            "action": str(action or ""),
            "resume_fingerprint": str(resume_fingerprint or ""),
            "base_head_generation": int((base_head or {}).get("generation") or 0),
            "base_head_revision_id": str((base_head or {}).get("revision_id") or ""),
            "state_document": state_document,
            "artifact_bundle": artifact_bundle,
        }
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
        if not isinstance(pending, dict) or pending.get("schema_version") != 1:
            raise CorruptProjectArtifactError("待恢复产物清单格式无效。")
        return self.restore_snapshot_artifacts(
            project_id,
            pending.get("artifact_bundle"),
            destinations,
        )

    def clear_pending_artifact_set(self, project_id: str) -> None:
        self.project_pending_artifact_path(project_id).unlink(missing_ok=True)

    def commit_project_head(
        self,
        project_id: str,
        *,
        state_document: dict[str, Any],
        project_manifest: dict[str, Any],
        page_documents: dict[str, dict[str, Any]],
        artifact_files: dict[str, Path] | None = None,
        expected_generation: int | None = None,
        replace_prefixes: tuple[str, ...] = (),
        remove_logical_paths: set[str] | None = None,
    ) -> dict[str, Any]:
        normalized_project_id = self.validated_project_id(project_id)
        current_head = self.read_project_head(normalized_project_id)
        current_generation = int((current_head or {}).get("generation") or 0)
        if (
            expected_generation is not None
            and int(expected_generation) != current_generation
        ):
            raise ProjectHeadConflictError(
                expected_generation=int(expected_generation),
                actual_generation=current_generation,
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
            except OSError:
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

        for path in sorted(snapshots_dir.glob("*.json")):
            payload = self.read_json_file(path, {})
            if isinstance(payload, dict):
                payload["_path"] = str(path)
                manifests.append(payload)

        manifests.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return manifests

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
        manifests: list[dict[str, Any]],
    ) -> None:
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
            summaries.append(
                {
                    field: payload[field]
                    for field in self.PROJECT_INDEX_FIELDS
                    if field in payload
                }
            )
        self.write_project_index(summaries)
        return summaries
