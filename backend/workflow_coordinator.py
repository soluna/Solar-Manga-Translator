from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Protocol

from domain.project_state import ProjectState
from engine.project_workspace import PageWorkingSet, PreparedHeadUpdate, ProjectWorkspace
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


class RenderPageAdapter(Protocol):
    async def prepare_render_page(
        self,
        command: ProjectCommand,
        working_set: PageWorkingSet,
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


class WorkflowCoordinator:
    def __init__(
        self,
        *,
        project_loader: ProjectLoader,
        execution_adapter: ExecutionAdapter,
        project_view_builder: ProjectViewBuilder,
        project_workspace: ProjectWorkspace | None = None,
        render_page_adapter: RenderPageAdapter | None = None,
    ) -> None:
        self._project_loader = project_loader
        self._execution_adapter = execution_adapter
        self._project_view_builder = project_view_builder
        self._project_workspace = project_workspace
        self._render_page_adapter = render_page_adapter

    async def execute(
        self,
        command: ProjectCommand,
        *,
        progress: ProgressCallback = NOOP_PROGRESS,
    ) -> dict[str, Any]:
        project = self._project_loader(command.project_id)
        if command.action == "rerender" and command.target_stored_name:
            if self._project_workspace is None or self._render_page_adapter is None:
                raise RuntimeError(
                    "RenderPage requires an explicitly assembled ProjectWorkspace "
                    "and render adapter"
                )
            return await self._execute_render_page(command, project, progress)
        result = await self._execution_adapter.execute(command, project, progress)
        return self._build_completion_payload(command.project_id, project, result)

    async def _execute_render_page(
        self,
        command: ProjectCommand,
        shared_project: dict[str, Any],
        progress: ProgressCallback,
    ) -> dict[str, Any]:
        assert command.target_stored_name is not None
        assert self._project_workspace is not None
        assert self._render_page_adapter is not None
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
            prepared = await self._render_page_adapter.prepare_render_page(
                command,
                working_set,
                best_effort_progress,
            )
            committed = self._project_workspace.commit_page_working_set(
                working_set,
                prepared,
            )

        try:
            committed_state = self._project_workspace.read_project_state_from_head(
                command.project_id,
                committed.head,
            )
            committed_project = ProjectState.load(
                committed_state,
                expected_project_id=command.project_id,
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
                command.project_id,
                committed_project,
                result,
            )
        except Exception as exc:
            result["warnings"].append(
                "Project Head committed but client view construction failed: "
                f"{exc}"
            )
            return {
                "session_id": command.project_id,
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
