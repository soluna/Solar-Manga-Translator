from __future__ import annotations

import asyncio
import inspect
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from workflow_coordinator import (
    ProjectCommand,
    TranslatorEngineWorkflowAdapter,
    WorkflowCoordinator,
)
from workflow_events import (
    TASK_ACTION_ALIASES,
    WORKFLOW_ACTIONS,
    UnsupportedWorkflowActionError,
)
import main


class DeterministicExecutionAdapter:
    async def execute(self, command, project, progress):
        await progress({"event": "status", "message": "adapter running"})
        project["workflow_stage"] = "translated"
        project["download_url"] = f"/api/download/{command.project_id}"
        return {
            "workflow_stage": "stale-adapter-stage",
            "download_url": "/api/download/stale-adapter-project",
            "project": {"is_busy": True, "busy_action": "stale-adapter"},
        }


class WorkflowCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_uses_noop_progress_by_default(self) -> None:
        class ProgressAdapter:
            async def execute(self, command, project, progress):
                await progress({"event": "status", "message": "ignored"})
                return {}

        coordinator = WorkflowCoordinator(
            project_loader=lambda project_id: {"project_id": project_id},
            execution_adapter=ProgressAdapter(),
            project_view_builder=lambda project_id, _project: {
                "session_id": project_id,
            },
        )

        result = await coordinator.execute(
            ProjectCommand(project_id="project-a", action="detect", config={})
        )

        self.assertEqual(result, {"session_id": "project-a"})

    async def test_execute_propagates_failure_or_cancellation_without_building_view(
        self,
    ) -> None:
        class RaisingAdapter:
            def __init__(self, error: BaseException) -> None:
                self.error = error

            async def execute(self, command, project, progress):
                raise self.error

        async def ignore_progress(_event: dict[str, Any]) -> None:
            return None

        for error in (RuntimeError("adapter failed"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                view_calls: list[str] = []
                coordinator = WorkflowCoordinator(
                    project_loader=lambda project_id: {"project_id": project_id},
                    execution_adapter=RaisingAdapter(error),
                    project_view_builder=lambda project_id, _project: (
                        view_calls.append(project_id) or {}
                    ),
                )

                with self.assertRaises(type(error)) as raised:
                    await coordinator.execute(
                        ProjectCommand(
                            project_id="project-a",
                            action="detect",
                            config={},
                        ),
                        progress=ignore_progress,
                    )

                self.assertIs(raised.exception, error)
                self.assertEqual(view_calls, [])

    def test_coordinator_has_one_public_business_method(self) -> None:
        public_methods = {
            name
            for name, value in vars(WorkflowCoordinator).items()
            if callable(value) and not name.startswith("_")
        }

        self.assertEqual(public_methods, {"execute"})

    def test_project_command_preserves_only_positive_expected_page_revision(self) -> None:
        self.assertIsNone(
            ProjectCommand(
                project_id="project-a",
                action="detect",
                config={},
            ).expected_page_revision
        )
        self.assertEqual(
            ProjectCommand(
                project_id="project-a",
                action="translate-page",
                config={},
                target_stored_name="page-1.png",
                expected_page_revision=3,
            ).expected_page_revision,
            3,
        )
        for invalid_revision in (True, False, "3", 0, -1):
            with self.subTest(revision=invalid_revision):
                with self.assertRaises(ValueError):
                    ProjectCommand(
                        project_id="project-a",
                        action="translate-page",
                        config={},
                        target_stored_name="page-1.png",
                        expected_page_revision=invalid_revision,
                    )

        for action, target in (
            ("detect", None),
            ("translate", None),
            ("resume-translate", None),
            ("rerender", None),
        ):
            with self.subTest(action=action):
                with self.assertRaises(ValueError):
                    ProjectCommand(
                        project_id="project-a",
                        action=action,
                        config={},
                        target_stored_name=target,
                        expected_page_revision=3,
                    )

    async def test_production_adapter_maps_every_canonical_action_explicitly(
        self,
    ) -> None:
        engine = mock.Mock()
        for method_name in (
            "rerender_session",
            "detect_session",
            "resume_translation_session",
            "translate_session",
        ):
            setattr(engine, method_name, mock.AsyncMock(return_value={}))
        adapter = TranslatorEngineWorkflowAdapter(engine)

        async def ignore_progress(_event: dict[str, Any]) -> None:
            return None

        cases = (
            ("rerender", "page-1.png", "rerender_session", {}),
            ("detect", None, "detect_session", {}),
            (
                "resume-translate",
                None,
                "resume_translation_session",
                {"skip_completed": True},
            ),
            (
                "translate-page",
                "page-1.png",
                "resume_translation_session",
                {"target_stored_name": "page-1.png"},
            ),
            ("translate", None, "translate_session", {}),
        )
        self.assertEqual({case[0] for case in cases}, set(WORKFLOW_ACTIONS))
        for action, target, expected_method, expected_extra in cases:
            with self.subTest(action=action):
                engine.reset_mock()
                command = ProjectCommand(
                    project_id="project-a",
                    action=action,
                    config={"target_lang": "CHS"},
                    target_stored_name=target,
                )

                await adapter.execute(command, {"project": "loaded"}, ignore_progress)

                called_method = getattr(engine, expected_method)
                called_method.assert_awaited_once()
                kwargs = called_method.await_args.kwargs
                self.assertEqual(kwargs["session_id"], "project-a")
                self.assertEqual(kwargs["session"], {"project": "loaded"})
                self.assertEqual(kwargs["raw_config"], {"target_lang": "CHS"})
                self.assertIs(kwargs["progress_callback"], ignore_progress)
                for key, value in expected_extra.items():
                    self.assertEqual(kwargs[key], value)

                awaited_methods = [
                    method_name
                    for method_name in (
                        "rerender_session",
                        "detect_session",
                        "resume_translation_session",
                        "translate_session",
                    )
                    if getattr(engine, method_name).await_count
                ]
                self.assertEqual(awaited_methods, [expected_method])

    async def test_main_runner_delegates_without_capturing_legacy_session(self) -> None:
        loaded_project = {
            "title": "Coordinator project",
            "workflow_stage": "detected",
        }
        coordinator = WorkflowCoordinator(
            project_loader=lambda project_id: {
                **loaded_project,
                "project_id": project_id,
            },
            execution_adapter=DeterministicExecutionAdapter(),
            project_view_builder=lambda project_id, loaded: {
                "session_id": project_id,
                "workflow_stage": loaded["workflow_stage"],
                "download_url": loaded["download_url"],
                "project": {
                    "title": loaded["title"],
                    "is_busy": True,
                    "busy_action": "rerender",
                },
            },
        )
        captured: dict[str, Any] = {}

        def capture_task_start(project_id, action, runner, *, metadata=None):
            captured.update(
                {
                    "project_id": project_id,
                    "action": action,
                    "runner": runner,
                    "metadata": metadata,
                }
            )
            return "task-a"

        legacy_session = {"title": "Legacy session must be ignored"}
        with (
            mock.patch.object(main, "workflow_coordinator", coordinator),
            mock.patch.object(
                main.translator_engine,
                "try_mark_session_busy",
                return_value=True,
            ),
            mock.patch.object(
                main.translator_engine,
                "clear_session_busy",
            ) as clear_busy,
            mock.patch.object(
                main.task_manager,
                "start",
                side_effect=capture_task_start,
            ),
        ):
            task_id = main.start_translation_task(
                session_id="project-a",
                session=legacy_session,
                action="render",
                config={"target_lang": "CHS"},
                target_stored_name="page-1.png",
            )
            progress_events: list[dict[str, Any]] = []

            async def collect_progress(event: dict[str, Any]) -> None:
                progress_events.append(event)

            completed = await captured["runner"](collect_progress)

        self.assertEqual(task_id, "task-a")
        self.assertEqual(captured["project_id"], "project-a")
        self.assertEqual(captured["action"], "rerender")
        self.assertEqual(
            captured["metadata"],
            {"target_stored_name": "page-1.png"},
        )
        self.assertNotIn(
            "session",
            inspect.getclosurevars(captured["runner"]).nonlocals,
        )
        self.assertEqual(completed["project"]["title"], "Coordinator project")
        self.assertFalse(completed["project"]["is_busy"])
        self.assertEqual(completed["project"]["busy_action"], "")
        self.assertEqual(completed["download_url"], "/api/download/project-a")
        self.assertEqual(
            progress_events,
            [{"event": "status", "message": "adapter running"}],
        )
        clear_busy.assert_called_once_with("project-a")

    def test_main_rejects_invalid_command_before_busy_or_task_start(self) -> None:
        with (
            mock.patch.object(
                main.translator_engine,
                "try_mark_session_busy",
            ) as mark_busy,
            mock.patch.object(main.task_manager, "start") as start_task,
        ):
            with self.assertRaises(ValueError):
                main.start_translation_task(
                    session_id="project-a",
                    session={"legacy": "ignored"},
                    action="translate-page",
                    config={},
                    target_stored_name="",
                )

        mark_busy.assert_not_called()
        start_task.assert_not_called()

    def test_main_releases_busy_when_task_registration_fails(self) -> None:
        with (
            mock.patch.object(
                main.translator_engine,
                "try_mark_session_busy",
                return_value=True,
            ),
            mock.patch.object(
                main.translator_engine,
                "clear_session_busy",
            ) as clear_busy,
            mock.patch.object(
                main.task_manager,
                "start",
                side_effect=RuntimeError("registration failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "registration failed"):
                main.start_translation_task(
                    session_id="project-a",
                    action="detect",
                    config={},
                    target_stored_name="",
                )

        clear_busy.assert_called_once_with("project-a")

    def test_project_command_is_frozen_and_canonicalizes_supported_actions(self) -> None:
        for action in WORKFLOW_ACTIONS:
            with self.subTest(canonical=action):
                command = ProjectCommand(
                    project_id="project-a",
                    action=action,
                    config={},
                    target_stored_name=(
                        "page-1.png" if action == "translate-page" else None
                    ),
                )
                self.assertEqual(command.action, action)

        for alias, canonical in TASK_ACTION_ALIASES.items():
            with self.subTest(alias=alias):
                command = ProjectCommand(
                    project_id="project-a",
                    action=alias,
                    config={},
                    target_stored_name=(
                        "page-1.png" if canonical == "translate-page" else None
                    ),
                )
                self.assertEqual(command.action, canonical)

        for invalid_action in ("", "unknown-action", None):
            with self.subTest(invalid=invalid_action):
                with self.assertRaises(UnsupportedWorkflowActionError):
                    ProjectCommand(
                        project_id="project-a",
                        action=invalid_action,
                        config={},
                    )

        source_config = {
            "target_lang": "CHS",
            "translation_region_overrides": {"region-a": "before"},
        }
        command = ProjectCommand(
            project_id="project-a",
            action="resume",
            config=source_config,
        )
        source_config["target_lang"] = "CHT"
        source_config["translation_region_overrides"]["region-a"] = "after"
        self.assertEqual(command.config["target_lang"], "CHS")
        self.assertEqual(
            command.config["translation_region_overrides"],
            {"region-a": "before"},
        )
        with self.assertRaises(FrozenInstanceError):
            command.action = "translate"  # type: ignore[misc]
        with self.assertRaises(TypeError):
            command.config["target_lang"] = "CHT"

    def test_project_command_rejects_invalid_page_scope(self) -> None:
        invalid_commands = (
            {"action": "translate-page", "target_stored_name": None},
            {"action": "detect", "target_stored_name": "page-1.png"},
            {"action": "translate", "target_stored_name": "page-1.png"},
            {"action": "resume-translate", "target_stored_name": "page-1.png"},
        )

        for fields in invalid_commands:
            with self.subTest(**fields):
                with self.assertRaises(ValueError):
                    ProjectCommand(
                        project_id="project-a",
                        config={},
                        **fields,
                    )

        self.assertIsNone(
            ProjectCommand(
                project_id="project-a",
                action="rerender",
                config={},
            ).target_stored_name
        )
        self.assertEqual(
            ProjectCommand(
                project_id="project-a",
                action="rerender",
                config={},
                target_stored_name=" page-1.png ",
            ).target_stored_name,
            "page-1.png",
        )

    async def test_execute_owns_completion_payload_construction(self) -> None:
        project = {"title": "Loaded project", "workflow_stage": "detected"}
        progress_events: list[dict[str, Any]] = []

        async def collect_progress(event: dict[str, Any]) -> None:
            progress_events.append(event)

        coordinator = WorkflowCoordinator(
            project_loader=lambda project_id: {
                **project,
                "project_id": project_id,
            },
            execution_adapter=DeterministicExecutionAdapter(),
            project_view_builder=lambda project_id, loaded: {
                "session_id": project_id,
                "loaded_title": loaded["title"],
                "workflow_stage": loaded["workflow_stage"],
                "download_url": loaded["download_url"],
                "project": {
                    "is_busy": True,
                    "busy_action": "resume-translate",
                },
            },
        )

        result = await coordinator.execute(
            ProjectCommand(
                project_id="project-a",
                action="resume-translate",
                config={"target_lang": "CHS"},
            ),
            progress=collect_progress,
        )

        self.assertEqual(
            result,
            {
                "session_id": "project-a",
                "loaded_title": "Loaded project",
                "workflow_stage": "translated",
                "download_url": "/api/download/project-a",
                "project": {
                    "is_busy": False,
                    "busy_action": "",
                },
            },
        )
        self.assertEqual(
            progress_events,
            [{"event": "status", "message": "adapter running"}],
        )


if __name__ == "__main__":
    unittest.main()
