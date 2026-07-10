from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


PROJECT_ARTIFACT_SCHEMA_VERSION = 2


class ArtifactTransitionError(ValueError):
    pass


class ProjectArtifactSchemaError(ValueError):
    pass


class UnsupportedProjectArtifactSchemaError(ProjectArtifactSchemaError):
    pass


class PageArtifactEvent(StrEnum):
    SOURCE_REPLACED = "source_replaced"
    RECOGNIZED = "recognized"
    BLANK_REPLACED = "blank_replaced"
    BLANK_EDITED = "blank_edited"
    TRANSLATED = "translated"
    TRANSLATION_EDITED = "translation_edited"
    LAYOUT_EDITED = "layout_edited"
    RENDERED = "rendered"


class ArtifactRevision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int = Field(default=0, ge=0)
    content_hash: str = ""
    derived_from: dict[str, int] = Field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.revision > 0

    def is_current(self, dependencies: dict[str, int]) -> bool:
        return self.ready and self.derived_from == dependencies


class PageArtifactState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_id: str = Field(min_length=1)
    source: ArtifactRevision = Field(
        default_factory=lambda: ArtifactRevision(revision=1)
    )
    recognition: ArtifactRevision = Field(default_factory=ArtifactRevision)
    blank: ArtifactRevision = Field(default_factory=ArtifactRevision)
    translation: ArtifactRevision = Field(default_factory=ArtifactRevision)
    final: ArtifactRevision = Field(default_factory=ArtifactRevision)
    layout_revision: int = Field(default=1, ge=1)

    def apply(self, event: PageArtifactEvent) -> PageArtifactState:
        if event == PageArtifactEvent.SOURCE_REPLACED:
            return PageArtifactState(
                page_id=self.page_id,
                source=ArtifactRevision(revision=self.source.revision + 1),
                layout_revision=self.layout_revision,
            )

        if event == PageArtifactEvent.RECOGNIZED:
            recognition = ArtifactRevision(
                revision=self.recognition.revision + 1,
                derived_from={"source": self.source.revision},
            )
            blank = ArtifactRevision(
                revision=self.blank.revision + 1,
                derived_from={
                    "source": self.source.revision,
                    "recognition": recognition.revision,
                },
            )
            return self.model_copy(
                update={
                    "recognition": recognition,
                    "blank": blank,
                    "translation": ArtifactRevision(),
                    "final": ArtifactRevision(),
                }
            )

        if event == PageArtifactEvent.BLANK_REPLACED:
            return self.model_copy(
                update={
                    "blank": ArtifactRevision(
                        revision=self.blank.revision + 1,
                        derived_from={"source": self.source.revision},
                    )
                }
            )

        if event == PageArtifactEvent.BLANK_EDITED:
            dependencies = self._current_blank_dependencies()
            if not self.blank.is_current(dependencies):
                dependencies = {"source": self.source.revision}
            return self.model_copy(
                update={
                    "blank": ArtifactRevision(
                        revision=self.blank.revision + 1,
                        derived_from=dependencies,
                    )
                }
            )

        if event in {PageArtifactEvent.TRANSLATED, PageArtifactEvent.TRANSLATION_EDITED}:
            self._require_translation_inputs(event)
            translation = ArtifactRevision(
                revision=self.translation.revision + 1,
                derived_from=self._translation_dependencies(),
            )
            updates: dict[str, object] = {"translation": translation}
            if event == PageArtifactEvent.TRANSLATED:
                updates["final"] = ArtifactRevision(
                    revision=self.final.revision + 1,
                    derived_from=self._final_dependencies(translation=translation),
                )
            return self.model_copy(update=updates)

        if event == PageArtifactEvent.LAYOUT_EDITED:
            return self.model_copy(update={"layout_revision": self.layout_revision + 1})

        if event == PageArtifactEvent.RENDERED:
            self._require_render_inputs(event)
            return self.model_copy(
                update={
                    "final": ArtifactRevision(
                        revision=self.final.revision + 1,
                        derived_from=self._final_dependencies(),
                    )
                }
            )

        raise ArtifactTransitionError(f"Unsupported page artifact event: {event}")

    def view(self) -> PageArtifactView:
        recognition_ready = self.recognition.is_current(
            self._recognition_dependencies()
        )
        blank_ready = self.blank.is_current(
            self._current_blank_dependencies()
        )
        translation_ready = recognition_ready and self.translation.is_current(
            self._translation_dependencies()
        )
        final_ready = (
            blank_ready
            and translation_ready
            and self.final.is_current(self._final_dependencies())
        )
        final_available = self.final.ready
        return PageArtifactView(
            page_id=self.page_id,
            artifacts=PageArtifactsView(
                source=ArtifactView.from_revision(self.source, current=self.source.ready),
                recognition=ArtifactView.from_revision(
                    self.recognition,
                    current=recognition_ready,
                ),
                blank=ArtifactView.from_revision(self.blank, current=blank_ready),
                translation=ArtifactView.from_revision(
                    self.translation,
                    current=translation_ready,
                ),
                final=ArtifactView.from_revision(self.final, current=final_ready),
            ),
            capabilities=PageArtifactCapabilities(
                recognition_ready=recognition_ready,
                blank_ready=blank_ready,
                translation_ready=translation_ready,
                final_available=final_available,
                final_ready=final_ready,
                final_stale=final_available and not final_ready,
                can_review_recognition=recognition_ready and blank_ready,
                can_translate=recognition_ready and blank_ready,
                can_render=blank_ready and translation_ready,
                can_export=final_ready,
            ),
        )

    def _recognition_dependencies(self) -> dict[str, int]:
        return {"source": self.source.revision}

    def _current_blank_dependencies(self) -> dict[str, int]:
        dependencies = {"source": self.source.revision}
        if "recognition" in self.blank.derived_from:
            dependencies["recognition"] = self.recognition.revision
        return dependencies

    def _translation_dependencies(self) -> dict[str, int]:
        return {"recognition": self.recognition.revision}

    def _final_dependencies(
        self,
        *,
        translation: ArtifactRevision | None = None,
    ) -> dict[str, int]:
        resolved_translation = translation or self.translation
        return {
            "blank": self.blank.revision,
            "translation": resolved_translation.revision,
            "layout": self.layout_revision,
        }

    def _require_current_recognition(self, event: PageArtifactEvent) -> None:
        if not self.recognition.is_current(self._recognition_dependencies()):
            raise ArtifactTransitionError(
                f"{event.value} requires current recognition for page {self.page_id}"
            )

    def _require_translation_inputs(self, event: PageArtifactEvent) -> None:
        self._require_current_recognition(event)
        if not self.blank.is_current(self._current_blank_dependencies()):
            raise ArtifactTransitionError(
                f"{event.value} requires a current blank artifact for page {self.page_id}"
            )

    def _require_render_inputs(self, event: PageArtifactEvent) -> None:
        self._require_translation_inputs(event)
        if not self.translation.is_current(self._translation_dependencies()):
            raise ArtifactTransitionError(
                f"{event.value} requires current translation for page {self.page_id}"
            )


class ArtifactView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int = Field(ge=0)
    ready: bool
    current: bool
    stale: bool
    content_hash: str = ""
    derived_from: dict[str, int] = Field(default_factory=dict)

    @classmethod
    def from_revision(
        cls,
        artifact: ArtifactRevision,
        *,
        current: bool,
    ) -> ArtifactView:
        return cls(
            revision=artifact.revision,
            ready=artifact.ready,
            current=current,
            stale=artifact.ready and not current,
            content_hash=artifact.content_hash,
            derived_from=dict(artifact.derived_from),
        )


class PageArtifactsView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: ArtifactView
    recognition: ArtifactView
    blank: ArtifactView
    translation: ArtifactView
    final: ArtifactView


class PageArtifactCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recognition_ready: bool
    blank_ready: bool
    translation_ready: bool
    final_available: bool
    final_ready: bool
    final_stale: bool
    can_review_recognition: bool
    can_translate: bool
    can_render: bool
    can_export: bool


class PageArtifactView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_id: str
    artifacts: PageArtifactsView
    capabilities: PageArtifactCapabilities


class LegacyPageArtifactEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    page_id: str = Field(min_length=1)
    document_revision: int = Field(default=1, ge=0)
    recognition_ready: bool = False
    blank_ready: bool = False
    translation_ready: bool = False
    final_ready: bool = False


class ProjectArtifactState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[PROJECT_ARTIFACT_SCHEMA_VERSION] = (
        PROJECT_ARTIFACT_SCHEMA_VERSION
    )
    pages: dict[str, PageArtifactState] = Field(default_factory=dict)

    @model_validator(mode="after")
    def page_keys_match_page_ids(self) -> ProjectArtifactState:
        mismatched = [
            page_id
            for page_id, page in self.pages.items()
            if page_id != page.page_id
        ]
        if mismatched:
            raise ValueError(
                "Page artifact keys must match embedded page ids: "
                + ", ".join(mismatched)
            )
        return self

    @classmethod
    def create(cls, page_ids: list[str]) -> ProjectArtifactState:
        normalized_ids = [str(page_id or "").strip() for page_id in page_ids]
        if any(not page_id for page_id in normalized_ids):
            raise ValueError("Page artifact state requires non-empty page ids")
        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError("Page artifact state requires unique page ids")
        return cls(
            pages={
                page_id: PageArtifactState(page_id=page_id)
                for page_id in normalized_ids
            }
        )

    @classmethod
    def load(
        cls,
        raw_state: object,
        *,
        legacy_pages: list[LegacyPageArtifactEvidence],
    ) -> ProjectArtifactState:
        if raw_state is None:
            return cls._from_legacy(legacy_pages)
        if not isinstance(raw_state, dict):
            raise ProjectArtifactSchemaError(
                "Project artifact state must be a JSON object"
            )

        raw_version = raw_state.get("schema_version")
        if raw_version != PROJECT_ARTIFACT_SCHEMA_VERSION:
            raise UnsupportedProjectArtifactSchemaError(
                "Unsupported project artifact schema version: "
                f"{raw_version!r}"
            )
        try:
            state = cls.model_validate(raw_state)
        except ValidationError as exc:
            raise ProjectArtifactSchemaError(
                "Project artifact state is invalid"
            ) from exc

        expected_ids = [page.page_id for page in legacy_pages]
        cls._validate_page_ids(expected_ids)
        reconciled_pages = {
            page_id: state.pages.get(page_id) or PageArtifactState(page_id=page_id)
            for page_id in expected_ids
        }
        if reconciled_pages == state.pages:
            return state
        return state.model_copy(update={"pages": reconciled_pages})

    @classmethod
    def _from_legacy(
        cls,
        legacy_pages: list[LegacyPageArtifactEvidence],
    ) -> ProjectArtifactState:
        page_ids = [page.page_id for page in legacy_pages]
        cls._validate_page_ids(page_ids)
        return cls(
            pages={
                evidence.page_id: cls._legacy_page_state(evidence)
                for evidence in legacy_pages
            }
        )

    @staticmethod
    def _legacy_page_state(
        evidence: LegacyPageArtifactEvidence,
    ) -> PageArtifactState:
        revision = max(1, evidence.document_revision)
        source = ArtifactRevision(revision=1)
        recognition = (
            ArtifactRevision(
                revision=revision,
                derived_from={"source": source.revision},
            )
            if evidence.recognition_ready
            else ArtifactRevision()
        )
        blank = (
            ArtifactRevision(
                revision=revision,
                derived_from={
                    "source": source.revision,
                    "recognition": recognition.revision,
                },
            )
            if evidence.blank_ready
            else ArtifactRevision()
        )
        translation = (
            ArtifactRevision(
                revision=revision,
                derived_from={"recognition": recognition.revision},
            )
            if evidence.translation_ready
            else ArtifactRevision()
        )
        final = (
            ArtifactRevision(
                revision=revision,
                derived_from={
                    "blank": blank.revision,
                    "translation": translation.revision,
                    "layout": 1,
                },
            )
            if evidence.final_ready
            else ArtifactRevision()
        )
        return PageArtifactState(
            page_id=evidence.page_id,
            source=source,
            recognition=recognition,
            blank=blank,
            translation=translation,
            final=final,
        )

    @staticmethod
    def _validate_page_ids(page_ids: list[str]) -> None:
        if any(not page_id for page_id in page_ids):
            raise ProjectArtifactSchemaError(
                "Project artifact state requires non-empty page ids"
            )
        if len(set(page_ids)) != len(page_ids):
            raise ProjectArtifactSchemaError(
                "Project artifact state requires unique page ids"
            )

    def apply(
        self,
        page_id: str,
        event: PageArtifactEvent,
    ) -> ProjectArtifactState:
        normalized_page_id = str(page_id or "").strip()
        page = self.pages.get(normalized_page_id)
        if page is None:
            raise KeyError(normalized_page_id)
        pages = dict(self.pages)
        pages[normalized_page_id] = page.apply(event)
        return self.model_copy(update={"pages": pages})

    def page_view(self, page_id: str) -> PageArtifactView:
        normalized_page_id = str(page_id or "").strip()
        page = self.pages.get(normalized_page_id)
        if page is None:
            raise KeyError(normalized_page_id)
        return page.view()
