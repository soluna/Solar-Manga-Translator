from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain.project_artifacts import (
    LegacyPageArtifactEvidence,
    ProjectArtifactState,
)


PROJECT_STATE_SCHEMA_VERSION = 2


class ProjectStateError(ValueError):
    """Base error for persisted project state that cannot be safely restored."""


class CorruptProjectStateError(ProjectStateError):
    """The project state file is unreadable or is not a JSON object."""


class InvalidProjectStateError(ProjectStateError):
    """The project state document violates the supported schema."""


class UnsupportedProjectStateVersionError(ProjectStateError):
    """The project state was written by an unsupported schema version."""


class SourceImageState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    stored_name: str = Field(min_length=1)
    url: str = ""


class ProjectState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[PROJECT_STATE_SCHEMA_VERSION] = PROJECT_STATE_SCHEMA_VERSION
    project_id: str = Field(min_length=1)
    project_title: str = ""
    project_note: str = ""
    review_mode: Literal["classic", "canvas_beta"] = "classic"
    project_created_at: str = ""
    project_updated_at: str = ""
    source_dir: str = ""
    translated_dir: str = ""
    source_images: list[SourceImageState] = Field(default_factory=list)
    download_path: str = ""
    translated_output_map: dict[str, str] = Field(default_factory=dict)
    rerender_generation: int = Field(default=0, ge=0)
    manual_regions: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    advanced_erase_pages: dict[str, dict[str, Any]] = Field(default_factory=dict)
    project_glossary: dict[str, Any] = Field(default_factory=dict)
    workflow_stage: Literal[
        "idle",
        "detecting",
        "detected",
        "translating",
        "translated",
    ] = "idle"
    mask_debug_dir: str = ""
    rerender_cache_dir: str = ""
    last_config: dict[str, Any] = Field(default_factory=dict)
    deferred_output_names: list[str] = Field(default_factory=list)
    translation_region_overrides: dict[str, str] = Field(default_factory=dict)
    translation_region_skip_overrides: dict[str, bool] = Field(default_factory=dict)
    translation_region_disabled_overrides: dict[str, bool] = Field(default_factory=dict)
    translation_region_layout_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    style_region_overrides: dict[str, str] = Field(default_factory=dict)
    artifact_state: ProjectArtifactState

    @model_validator(mode="after")
    def page_identifiers_are_unique_and_match_artifacts(self) -> ProjectState:
        page_ids = [image.stored_name for image in self.source_images]
        if len(page_ids) != len(set(page_ids)):
            raise ValueError("Project source image identifiers must be unique")
        if set(page_ids) != set(self.artifact_state.pages):
            raise ValueError(
                "Project source images and artifact pages must contain the same identifiers"
            )
        return self

    @classmethod
    def capture(
        cls,
        *,
        project_id: str,
        session: Mapping[str, Any],
        artifact_state: ProjectArtifactState,
    ) -> ProjectState:
        try:
            return cls.model_validate(
                {
                    "schema_version": PROJECT_STATE_SCHEMA_VERSION,
                    "project_id": project_id,
                    "project_title": str(session.get("project_title") or ""),
                    "project_note": str(session.get("project_note") or ""),
                    "review_mode": str(session.get("review_mode") or "classic"),
                    "project_created_at": str(session.get("project_created_at") or ""),
                    "project_updated_at": str(session.get("project_updated_at") or ""),
                    "source_dir": str(session.get("source_dir") or ""),
                    "translated_dir": str(session.get("translated_dir") or ""),
                    "source_images": list(session.get("source_images") or []),
                    "download_path": str(session.get("download_path") or ""),
                    "translated_output_map": dict(session.get("translated_output_map") or {}),
                    "rerender_generation": int(session.get("rerender_generation") or 0),
                    "manual_regions": dict(session.get("manual_regions") or {}),
                    "advanced_erase_pages": dict(session.get("advanced_erase_pages") or {}),
                    "project_glossary": dict(session.get("project_glossary") or {}),
                    "workflow_stage": str(session.get("workflow_stage") or "idle"),
                    "mask_debug_dir": str(session.get("mask_debug_dir") or ""),
                    "rerender_cache_dir": str(session.get("rerender_cache_dir") or ""),
                    "last_config": dict(session.get("last_config") or {}),
                    "deferred_output_names": sorted(
                        str(item)
                        for item in (session.get("deferred_output_names") or [])
                    ),
                    "translation_region_overrides": dict(
                        session.get("translation_region_overrides") or {}
                    ),
                    "translation_region_skip_overrides": dict(
                        session.get("translation_region_skip_overrides") or {}
                    ),
                    "translation_region_disabled_overrides": dict(
                        session.get("translation_region_disabled_overrides") or {}
                    ),
                    "translation_region_layout_overrides": dict(
                        session.get("translation_region_layout_overrides") or {}
                    ),
                    "style_region_overrides": dict(
                        session.get("style_region_overrides") or {}
                    ),
                    "artifact_state": artifact_state,
                }
            )
        except (TypeError, ValueError) as exc:
            raise InvalidProjectStateError(
                "项目状态包含无法持久化的数据，请保留项目并导出诊断包。"
            ) from exc

    @classmethod
    def load(
        cls,
        raw_state: object,
        *,
        expected_project_id: str,
        legacy_artifact_state: ProjectArtifactState | None = None,
    ) -> ProjectState:
        if not isinstance(raw_state, dict):
            raise CorruptProjectStateError("项目状态文件的顶层内容必须是 JSON 对象。")
        raw_version = raw_state.get("schema_version")
        if raw_version is None or raw_version == 1:
            if not any(
                field in raw_state
                for field in ("project_id", "source_dir", "source_images")
            ):
                raise InvalidProjectStateError(
                    "旧项目状态缺少可识别的项目字段，无法安全迁移。"
                )
            persisted_project_id = str(raw_state.get("project_id") or "").strip()
            if persisted_project_id and persisted_project_id != expected_project_id:
                raise InvalidProjectStateError(
                    "项目状态中的项目标识与当前目录不一致，已停止恢复。"
                )
            if legacy_artifact_state is None:
                raise InvalidProjectStateError(
                    "旧项目缺少可迁移的页面产物状态，无法安全恢复。"
                )
            return cls.capture(
                project_id=expected_project_id,
                session=raw_state,
                artifact_state=legacy_artifact_state,
            )
        if raw_version != PROJECT_STATE_SCHEMA_VERSION:
            raise UnsupportedProjectStateVersionError(
                f"不支持的项目状态版本：{raw_version!r}。请升级应用后重试。"
            )
        if "artifact_state" not in raw_state:
            raise InvalidProjectStateError(
                "项目状态缺少页面产物信息，无法安全恢复。"
            )
        try:
            source_images = [
                SourceImageState.model_validate(image)
                for image in (raw_state.get("source_images") or [])
            ]
            artifact_state = ProjectArtifactState.load(
                raw_state.get("artifact_state"),
                legacy_pages=[
                    LegacyPageArtifactEvidence(page_id=image.stored_name)
                    for image in source_images
                ],
            )
            state = cls.model_validate(
                {
                    **raw_state,
                    "artifact_state": artifact_state.model_dump(mode="json"),
                }
            )
        except (TypeError, ValueError) as exc:
            raise InvalidProjectStateError(
                "项目状态内容不完整或格式错误，无法安全恢复。"
            ) from exc
        if state.project_id != expected_project_id:
            raise InvalidProjectStateError(
                "项目状态中的项目标识与当前目录不一致，已停止恢复。"
            )
        return state

    def to_runtime_session(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload.pop("schema_version", None)
        payload["deferred_output_names"] = set(self.deferred_output_names)
        return payload
