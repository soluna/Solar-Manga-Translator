from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol

from engine.translator import TranslatorEngine
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


class WorkflowCoordinator:
    def __init__(
        self,
        *,
        project_loader: ProjectLoader,
        execution_adapter: ExecutionAdapter,
        project_view_builder: ProjectViewBuilder,
    ) -> None:
        self._project_loader = project_loader
        self._execution_adapter = execution_adapter
        self._project_view_builder = project_view_builder

    async def execute(
        self,
        command: ProjectCommand,
        *,
        progress: ProgressCallback = NOOP_PROGRESS,
    ) -> dict[str, Any]:
        project = self._project_loader(command.project_id)
        result = await self._execution_adapter.execute(command, project, progress)
        execution_extras = {
            field: result[field]
            for field in _COMPLETION_EXTRA_FIELDS
            if field in result
        }
        completed_payload = {
            **execution_extras,
            **self._project_view_builder(command.project_id, project),
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
