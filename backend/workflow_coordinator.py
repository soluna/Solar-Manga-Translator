from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Protocol

from domain.project_state import ProjectState
from engine.project_workspace import (
    PageWorkingSet,
    PreparedHeadUpdate,
    ProjectHeadCommitResult,
    ProjectWorkingSet,
    ProjectWorkspace,
)
from engine.translator import (
    PageDocumentRevisionConflict,
    TranslatorEngine,
)
from workflow_events import ProjectCommand


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]
ProjectLoader = Callable[[str], dict[str, Any]]
ProjectViewBuilder = Callable[[str, dict[str, Any]], dict[str, Any]]
_COMPLETION_EXTRA_FIELDS = ("warnings", "reused_page_ids")


async def _noop_progress(_event: dict[str, Any]) -> None:
    return None


NOOP_PROGRESS: ProgressCallback = _noop_progress


class ExecutionAdapter(Protocol):
    async def execute(
        self,
        command: ProjectCommand,
        project: dict[str, Any],
        progress: ProgressCallback,
    ) -> dict[str, Any]: ...


class WorkflowPreparationAdapter(Protocol):
    def project_command_fingerprint(self, command: ProjectCommand) -> str: ...

    async def prepare_render_page(
        self,
        command: ProjectCommand,
        working_set: PageWorkingSet,
        progress: ProgressCallback,
    ) -> PreparedHeadUpdate: ...

    async def prepare_project_command(
        self,
        command: ProjectCommand,
        working_set: ProjectWorkingSet,
        progress: ProgressCallback,
    ) -> PreparedHeadUpdate: ...


class TranslatorEngineWorkflowAdapter:
    def __init__(self, engine: TranslatorEngine) -> None:
        self._engine = engine

    async def execute(
        self,
        command: ProjectCommand,
        project: dict[str, Any],
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        config = dict(command.config)
        if command.action == "rerender":
            return await self._engine.rerender_session(
                session_id=command.project_id,
                session=project,
                raw_config=config,
                progress_callback=progress,
                target_stored_name=command.target_stored_name,
            )
        if command.action == "detect":
            return await self._engine.detect_session(
                session_id=command.project_id,
                session=project,
                raw_config=config,
                progress_callback=progress,
            )
        if command.action == "resume-translate":
            return await self._engine.resume_translation_session(
                session_id=command.project_id,
                session=project,
                raw_config=config,
                progress_callback=progress,
                skip_completed=True,
            )
        if command.action == "translate-page":
            return await self._engine.resume_translation_session(
                session_id=command.project_id,
                session=project,
                raw_config=config,
                progress_callback=progress,
                target_stored_name=command.target_stored_name,
            )
        if command.action == "translate":
            return await self._engine.translate_session(
                session_id=command.project_id,
                session=project,
                raw_config=config,
                progress_callback=progress,
            )
        raise RuntimeError(f"Unsupported canonical Project Command: {command.action}")

    async def prepare_render_page(
        self,
        command: ProjectCommand,
        working_set: PageWorkingSet,
        progress: ProgressCallback,
    ) -> PreparedHeadUpdate:
        return await self._engine.render_page_working_set(
            working_set=working_set,
            raw_config=dict(command.config),
            progress_callback=progress,
        )

    async def prepare_project_command(
        self,
        command: ProjectCommand,
        working_set: ProjectWorkingSet,
        progress: ProgressCallback,
    ) -> PreparedHeadUpdate:
        return await self._engine.prepare_project_command_working_set(
            command=command,
            working_set=working_set,
            progress_callback=progress,
        )

    def project_command_fingerprint(self, command: ProjectCommand) -> str:
        return self._engine.project_command_fingerprint(
            action=command.action,
            raw_config=dict(command.config),
            target_stored_name=command.target_stored_name,
        )


class WorkflowCoordinator:
    def __init__(
        self,
        *,
        project_loader: ProjectLoader,
        execution_adapter: ExecutionAdapter,
        project_view_builder: ProjectViewBuilder,
        project_workspace: ProjectWorkspace | None = None,
        render_page_adapter: WorkflowPreparationAdapter | None = None,
    ) -> None:
        self._project_loader = project_loader
        self._execution_adapter = execution_adapter
        self._project_view_builder = project_view_builder
        self._project_workspace = project_workspace
        self._workflow_adapter = render_page_adapter

    async def execute(
        self,
        command: ProjectCommand,
        *,
        progress: ProgressCallback = NOOP_PROGRESS,
    ) -> dict[str, Any]:
        project = self._project_loader(command.project_id)
        if command.action == "rerender" and command.target_stored_name:
            if self._project_workspace is None or self._workflow_adapter is None:
                raise RuntimeError(
                    "RenderPage requires an explicitly assembled ProjectWorkspace "
                    "and render adapter"
                )
            return await self._execute_render_page(command, project, progress)
        if (
            self._project_workspace is not None
            and self._workflow_adapter is not None
            and command.action
            in {"rerender", "detect", "translate", "resume-translate", "translate-page"}
        ):
            return await self._execute_project_command(command, project, progress)
        result = await self._execution_adapter.execute(command, project, progress)
        return self._build_completion_payload(command.project_id, project, result)

    async def _execute_project_command(
        self,
        command: ProjectCommand,
        shared_project: dict[str, Any],
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        assert self._project_workspace is not None
        assert self._workflow_adapter is not None
        base = self._project_workspace.read_project_command_base(command.project_id)
        if command.target_stored_name is not None:
            page_document = base.page_documents.get(command.target_stored_name)
            if not isinstance(page_document, dict):
                raise FileNotFoundError(
                    f"Project Head 中不存在页面：{command.target_stored_name}"
                )
            actual_revision = page_document["metadata"]["revision"]
            expected_revision = (
                command.expected_page_revision
                if command.expected_page_revision is not None
                else actual_revision
            )
            if expected_revision != actual_revision:
                raise PageDocumentRevisionConflict(
                    expected_revision=expected_revision,
                    actual_revision=actual_revision,
                    document=page_document,
                )

        warnings: list[str] = []

        async def best_effort_progress(event: dict[str, Any]) -> None:
            try:
                await progress(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                warning = f"Progress delivery failed after workflow continued: {exc}"
                if warning not in warnings:
                    warnings.append(warning)

        with self._project_workspace.materialize_project_working_set(
            base,
            action=command.action,
            resume_fingerprint=(
                self._workflow_adapter.project_command_fingerprint(command)
            ),
            legacy_project=shared_project,
        ) as working_set:
            prepared = await self._workflow_adapter.prepare_project_command(
                command,
                working_set,
                best_effort_progress,
            )
            committed = self._project_workspace.commit_project_working_set(
                working_set,
                prepared,
            )
        return self._committed_outcome(
            command.project_id,
            shared_project,
            prepared,
            committed,
            warnings,
        )

    async def _execute_render_page(
        self,
        command: ProjectCommand,
        shared_project: dict[str, Any],
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        assert command.target_stored_name is not None
        assert self._project_workspace is not None
        assert self._workflow_adapter is not None
        base = self._project_workspace.read_command_base(
            command.project_id,
            command.target_stored_name,
        )
        expected_revision = (
            command.expected_page_revision
            if command.expected_page_revision is not None
            else base.page_revision
        )
        if expected_revision != base.page_revision:
            raise PageDocumentRevisionConflict(
                expected_revision=expected_revision,
                actual_revision=base.page_revision,
                document=base.page_document,
            )

        warnings: list[str] = []

        async def best_effort_progress(event: dict[str, Any]) -> None:
            try:
                await progress(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                warning = f"Progress delivery failed after render continued: {exc}"
                if warning not in warnings:
                    warnings.append(warning)

        with self._project_workspace.materialize_page_working_set(
            base,
            legacy_project=shared_project,
        ) as working_set:
            prepared = await self._workflow_adapter.prepare_render_page(
                command,
                working_set,
                best_effort_progress,
            )
            committed = self._project_workspace.commit_page_working_set(
                working_set,
                prepared,
            )
        return self._committed_outcome(
            command.project_id,
            shared_project,
            prepared,
            committed,
            warnings,
        )

    def _committed_outcome(
        self,
        project_id: str,
        shared_project: dict[str, Any],
        prepared: PreparedHeadUpdate,
        committed: ProjectHeadCommitResult,
        warnings: list[str],
    ) -> dict[str, Any]:
        assert self._project_workspace is not None
        try:
            committed_state = self._project_workspace.read_project_state_from_head(
                project_id,
                committed.head,
            )
            committed_project = ProjectState.load(
                committed_state,
                expected_project_id=project_id,
            ).to_runtime_session()
        except Exception as exc:
            warnings.append(
                "Project Head committed but outcome state reload failed; "
                f"using the committed preparation: {exc}"
            )
            committed_project = dict(committed.runtime_session)
        try:
            shared_project.clear()
            shared_project.update(committed_project)
        except Exception as exc:
            warnings.append(
                f"Project Head committed but shared session projection failed: {exc}"
            )
        result = {
            **prepared.execution_extras,
            "warnings": [
                *warnings,
                *committed.warnings,
                *list(prepared.execution_extras.get("warnings") or []),
            ],
            "project_head_generation": int(committed.head["generation"]),
            "project_head_revision_id": str(committed.head["revision_id"]),
        }
        try:
            return self._build_completion_payload(
                project_id,
                committed_project,
                result,
            )
        except Exception as exc:
            result["warnings"].append(
                "Project Head committed but client view construction failed: "
                f"{exc}"
            )
            return {
                "session_id": project_id,
                "workflow_stage": str(
                    committed_project.get("workflow_stage") or "translated"
                ),
                "warnings": result["warnings"],
                "project_head_generation": result["project_head_generation"],
                "project_head_revision_id": result["project_head_revision_id"],
                "project": {
                    "is_busy": False,
                    "busy_action": "",
                },
            }

    def _build_completion_payload(
        self,
        project_id: str,
        project: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        execution_extras = {
            field: result[field]
            for field in (*_COMPLETION_EXTRA_FIELDS, "project_head_generation", "project_head_revision_id")
            if field in result
        }
        completed_payload = {
            **self._project_view_builder(project_id, project),
            **execution_extras,
        }
        project_view = completed_payload.get("project")
        if isinstance(project_view, dict):
            completed_payload["project"] = {
                **project_view,
                "is_busy": False,
                "busy_action": "",
            }
        return completed_payload


__all__ = [
    "ProjectCommand",
    "TranslatorEngineWorkflowAdapter",
    "WorkflowCoordinator",
]
