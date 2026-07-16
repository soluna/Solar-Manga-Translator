from __future__ import annotations

import asyncio
import copy
import inspect
import sys
import tempfile
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
from domain.project_artifacts import PageArtifactEvent, ProjectArtifactState
from domain.project_state import ProjectState
from engine.project_workspace import (
    PreparedHeadUpdate,
    ProjectHeadConflictError,
    ProjectWorkspace,
)
from engine.translator import PageDocumentRevisionConflict
from runtime_paths import AppPaths
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


def make_workspace(root: Path) -> ProjectWorkspace:
    paths = AppPaths(
        code_dir=BACKEND_DIR,
        app_data_dir=root / "app-data",
        models_dir=root / "models",
        output_dir=root / "output",
        logs_dir=root / "logs",
        cache_dir=root / "cache",
        config_dir=root / "config",
    )
    paths.ensure_directories()
    return ProjectWorkspace(paths)


def seed_render_page_project(
    root: Path,
) -> tuple[ProjectWorkspace, dict[str, Any], dict[str, Any]]:
    workspace = make_workspace(root)
    project_id = "project-a"
    source_dir = root / "live-source"
    translated_dir = root / "live-translated"
    cache_dir = root / "live-cache"
    source_dir.mkdir(parents=True)
    translated_dir.mkdir(parents=True)
    (cache_dir / "001.png").mkdir(parents=True)
    (source_dir / "001.png").write_bytes(b"head source")
    (translated_dir / "001.png").write_bytes(b"old translated")
    (cache_dir / "001.png" / "regions.json").write_text(
        "old cache",
        encoding="utf-8",
    )
    artifact_state = ProjectArtifactState.create(["001.png"])
    artifact_state = artifact_state.apply("001.png", PageArtifactEvent.RECOGNIZED)
    artifact_state = artifact_state.apply("001.png", PageArtifactEvent.TRANSLATED)
    session = {
        "project_title": "Project A",
        "project_created_at": "2026-07-16T00:00:00+00:00",
        "project_updated_at": "2026-07-16T00:00:00+00:00",
        "source_dir": str(source_dir),
        "translated_dir": str(translated_dir),
        "rerender_cache_dir": str(cache_dir),
        "source_images": [{"name": "001.png", "stored_name": "001.png"}],
        "translated_output_map": {"001.png": "001.png"},
        "download_path": str(root / "existing.zip"),
        "workflow_stage": "translated",
        "last_config": {"rerender_output_format": "png"},
        "artifact_state": artifact_state.model_dump(mode="json"),
    }
    state_document = ProjectState.capture(
        project_id=project_id,
        session=session,
        artifact_state=artifact_state,
    ).model_dump(mode="json")
    page_document = {
        "page_id": "001.png",
        "regions": [],
        "metadata": {"document_version": 2, "revision": 4},
    }
    head = workspace.commit_project_head(
        project_id,
        state_document=state_document,
        project_manifest={
            "project_id": project_id,
            "title": "Project A",
            "workflow_stage": "translated",
        },
        page_documents={"001.png": page_document},
        artifact_files={
            "source/001.png": source_dir / "001.png",
            "translated/001.png": translated_dir / "001.png",
            "cache/001.png/regions.json": cache_dir / "001.png" / "regions.json",
        },
    )
    return workspace, ProjectState.load(
        state_document,
        expected_project_id=project_id,
    ).to_runtime_session(), head


class DeterministicRenderPageAdapter:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls = 0
        self.working_root: Path | None = None
        self.observed_source = b""

    async def prepare_render_page(self, command, working_set, progress):
        self.calls += 1
        self.working_root = working_set.root
        self.observed_source = (
            working_set.source_dir / working_set.base.page_id
        ).read_bytes()
        await progress({"event": "status", "message": "rendering"})
        output = working_set.translated_dir / working_set.base.page_id
        output.write_bytes(b"new translated")
        cache_file = (
            working_set.cache_dir
            / working_set.base.page_id
            / "regions.json"
        )
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text("new cache", encoding="utf-8")
        if self.error is not None:
            raise self.error
        state_document = copy.deepcopy(working_set.base.state_document)
        state_document["project_updated_at"] = "2026-07-16T00:01:00+00:00"
        state_document["translated_output_map"] = {
            working_set.base.page_id: working_set.base.page_id
        }
        page_document = copy.deepcopy(working_set.base.page_document)
        page_document["metadata"] = {
            **dict(page_document.get("metadata") or {}),
            "revision": working_set.base.page_revision + 1,
        }
        return PreparedHeadUpdate(
            state_document=state_document,
            project_manifest={
                **working_set.base.project_manifest,
                "updated_at": "2026-07-16T00:01:00+00:00",
            },
            page_documents={working_set.base.page_id: page_document},
            artifact_files={
                f"translated/{working_set.base.page_id}": output,
                f"cache/{working_set.base.page_id}/regions.json": cache_file,
            },
            replace_prefixes=(
                f"cache/{working_set.base.page_id}/",
                f"pages/{working_set.base.page_id}/",
            ),
            remove_logical_paths={
                f"translated/{working_set.base.page_id}"
            },
            runtime_session=ProjectState.load(
                state_document,
                expected_project_id=working_set.base.project_id,
            ).to_runtime_session(),
            execution_extras={"workflow_stage": "stale-adapter-stage"},
        )


class WorkflowCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def make_render_page_coordinator(
        self,
        workspace: ProjectWorkspace,
        shared_project: dict[str, Any],
        render_adapter: DeterministicRenderPageAdapter,
    ) -> WorkflowCoordinator:
        return WorkflowCoordinator(
            project_loader=lambda _project_id: shared_project,
            execution_adapter=DeterministicExecutionAdapter(),
            project_view_builder=lambda project_id, project: {
                "session_id": project_id,
                "workflow_stage": project["workflow_stage"],
                "page_revision": int(
                    (
                        workspace.read_project_page_document(
                            project_id,
                            "001.png",
                        ).get("metadata")
                        or {}
                    ).get("revision")
                    or 0
                ),
                "project": {
                    "is_busy": True,
                    "busy_action": "rerender",
                },
            },
            project_workspace=workspace,
            render_page_adapter=render_adapter,
        )

    async def test_render_page_commits_one_head_from_bound_working_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, shared_project, first_head = seed_render_page_project(root)
            render_adapter = DeterministicRenderPageAdapter()
            coordinator = self.make_render_page_coordinator(
                workspace,
                shared_project,
                render_adapter,
            )

            with mock.patch.object(
                workspace,
                "write_json_file",
                wraps=workspace.write_json_file,
            ) as write_json:
                result = await coordinator.execute(
                    ProjectCommand(
                        project_id="project-a",
                        action="rerender",
                        config={},
                        target_stored_name="001.png",
                        expected_page_revision=4,
                    )
                )

            second_head = workspace.read_project_head("project-a")
            self.assertEqual(render_adapter.calls, 1)
            self.assertEqual(render_adapter.observed_source, b"head source")
            self.assertIsNotNone(second_head)
            self.assertEqual(second_head["generation"], first_head["generation"] + 1)
            self.assertEqual(result["project_head_generation"], 2)
            self.assertEqual(result["page_revision"], 5)
            self.assertEqual(result["workflow_stage"], "translated")
            self.assertEqual(
                sum(
                    call.args[0] == workspace.project_head_path("project-a")
                    for call in write_json.call_args_list
                ),
                1,
            )
            for logical_path in (
                "state/session.json",
                "project/project.json",
                "pages/001.png/page_document.json",
                "translated/001.png",
            ):
                with self.subTest(logical_path=logical_path):
                    self.assertIn(logical_path, second_head["files"])
            self.assertEqual(
                (root / "live-translated" / "001.png").read_bytes(),
                b"new translated",
            )
            self.assertEqual(
                (root / "live-cache" / "001.png" / "regions.json").read_text(
                    encoding="utf-8"
                ),
                "new cache",
            )
            self.assertFalse(render_adapter.working_root.exists())

    async def test_render_page_rejects_stale_revision_before_materialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, shared_project, first_head = seed_render_page_project(
                Path(tmp)
            )
            render_adapter = DeterministicRenderPageAdapter()
            coordinator = self.make_render_page_coordinator(
                workspace,
                shared_project,
                render_adapter,
            )
            with mock.patch.object(
                workspace,
                "materialize_page_working_set",
                wraps=workspace.materialize_page_working_set,
            ) as materialize:
                with self.assertRaises(PageDocumentRevisionConflict) as raised:
                    await coordinator.execute(
                        ProjectCommand(
                            project_id="project-a",
                            action="rerender",
                            config={},
                            target_stored_name="001.png",
                            expected_page_revision=3,
                        )
                    )

            self.assertEqual(raised.exception.expected_revision, 3)
            self.assertEqual(raised.exception.actual_revision, 4)
            self.assertEqual(raised.exception.document["page_id"], "001.png")
            self.assertEqual(render_adapter.calls, 0)
            materialize.assert_not_called()
            self.assertEqual(workspace.read_project_head("project-a"), first_head)

    async def test_render_page_progress_failure_does_not_change_commit_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, shared_project, _first_head = seed_render_page_project(
                Path(tmp)
            )
            render_adapter = DeterministicRenderPageAdapter()
            coordinator = self.make_render_page_coordinator(
                workspace,
                shared_project,
                render_adapter,
            )

            async def disconnected_progress(_event: dict[str, Any]) -> None:
                raise RuntimeError("subscriber disconnected")

            result = await coordinator.execute(
                ProjectCommand(
                    project_id="project-a",
                    action="rerender",
                    config={},
                    target_stored_name="001.png",
                ),
                progress=disconnected_progress,
            )

            self.assertEqual(result["project_head_generation"], 2)
            self.assertEqual(result["page_revision"], 5)
            self.assertTrue(
                any("subscriber disconnected" in warning for warning in result["warnings"])
            )

    async def test_render_page_failure_or_cancel_leaves_head_and_live_files_unchanged(
        self,
    ) -> None:
        for error in (RuntimeError("render failed"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    workspace, shared_project, first_head = seed_render_page_project(
                        root
                    )
                    render_adapter = DeterministicRenderPageAdapter(error)
                    coordinator = self.make_render_page_coordinator(
                        workspace,
                        shared_project,
                        render_adapter,
                    )

                    with self.assertRaises(type(error)):
                        await coordinator.execute(
                            ProjectCommand(
                                project_id="project-a",
                                action="rerender",
                                config={},
                                target_stored_name="001.png",
                            )
                        )

                    self.assertEqual(
                        workspace.read_project_head("project-a"),
                        first_head,
                    )
                    self.assertEqual(
                        (root / "live-translated" / "001.png").read_bytes(),
                        b"old translated",
                    )
                    self.assertFalse(render_adapter.working_root.exists())

    async def test_render_page_post_commit_projection_failure_is_a_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, shared_project, _first_head = seed_render_page_project(
                Path(tmp)
            )
            render_adapter = DeterministicRenderPageAdapter()
            coordinator = self.make_render_page_coordinator(
                workspace,
                shared_project,
                render_adapter,
            )

            with mock.patch.object(
                workspace,
                "refresh_project_index_entry",
                side_effect=OSError("index unavailable"),
            ):
                result = await coordinator.execute(
                    ProjectCommand(
                        project_id="project-a",
                        action="rerender",
                        config={},
                        target_stored_name="001.png",
                    )
                )

            self.assertEqual(result["project_head_generation"], 2)
            self.assertEqual(result["page_revision"], 5)
            self.assertTrue(
                any("index unavailable" in warning for warning in result["warnings"])
            )

    async def test_render_page_view_failure_after_cas_still_returns_committed_success(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, shared_project, _first_head = seed_render_page_project(
                Path(tmp)
            )
            render_adapter = DeterministicRenderPageAdapter()

            def fail_view(_project_id, _project):
                raise OSError("view unavailable")

            coordinator = WorkflowCoordinator(
                project_loader=lambda _project_id: shared_project,
                execution_adapter=DeterministicExecutionAdapter(),
                project_view_builder=fail_view,
                project_workspace=workspace,
                render_page_adapter=render_adapter,
            )

            result = await coordinator.execute(
                ProjectCommand(
                    project_id="project-a",
                    action="rerender",
                    config={},
                    target_stored_name="001.png",
                )
            )

            self.assertEqual(result["project_head_generation"], 2)
            self.assertEqual(result["workflow_stage"], "translated")
            self.assertFalse(result["project"]["is_busy"])
            self.assertTrue(
                any("view unavailable" in warning for warning in result["warnings"])
            )

    async def test_render_page_artifact_projection_failure_keeps_head_readable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, shared_project, _first_head = seed_render_page_project(root)
            render_adapter = DeterministicRenderPageAdapter()
            coordinator = self.make_render_page_coordinator(
                workspace,
                shared_project,
                render_adapter,
            )
            original_restore = workspace.restore_snapshot_artifacts

            def fail_only_live_projection(project_id, bundle, destinations):
                if destinations.get("translated") == root / "live-translated":
                    raise OSError("live projection unavailable")
                return original_restore(project_id, bundle, destinations)

            with mock.patch.object(
                workspace,
                "restore_snapshot_artifacts",
                side_effect=fail_only_live_projection,
            ):
                result = await coordinator.execute(
                    ProjectCommand(
                        project_id="project-a",
                        action="rerender",
                        config={},
                        target_stored_name="001.png",
                    )
                )

            recovered = workspace.materialize_project_head_artifact(
                "project-a",
                "translated/001.png",
                root / "recovered" / "001.png",
            )
            self.assertEqual(result["project_head_generation"], 2)
            self.assertTrue(
                any(
                    "live projection unavailable" in warning
                    for warning in result["warnings"]
                )
            )
            self.assertEqual(recovered.read_bytes(), b"new translated")

    async def test_render_page_cas_conflict_preserves_intervening_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, shared_project, first_head = seed_render_page_project(root)

            class AdvancingRenderAdapter(DeterministicRenderPageAdapter):
                async def prepare_render_page(self, command, working_set, progress):
                    prepared = await super().prepare_render_page(
                        command,
                        working_set,
                        progress,
                    )
                    workspace.commit_project_head(
                        "project-a",
                        state_document=working_set.base.state_document,
                        project_manifest={
                            **working_set.base.project_manifest,
                            "title": "Intervening Head",
                        },
                        page_documents={
                            "001.png": working_set.base.page_document,
                        },
                        expected_generation=first_head["generation"],
                        expected_revision_id=first_head["revision_id"],
                    )
                    return prepared

            render_adapter = AdvancingRenderAdapter()
            coordinator = self.make_render_page_coordinator(
                workspace,
                shared_project,
                render_adapter,
            )

            with self.assertRaises(ProjectHeadConflictError):
                await coordinator.execute(
                    ProjectCommand(
                        project_id="project-a",
                        action="rerender",
                        config={},
                        target_stored_name="001.png",
                    )
                )

            final_head = workspace.read_project_head("project-a")
            final_manifest = workspace.read_project_manifest("project-a")
            self.assertEqual(render_adapter.calls, 1)
            self.assertEqual(final_head["generation"], 2)
            self.assertEqual(final_manifest["title"], "Intervening Head")
            self.assertFalse(render_adapter.working_root.exists())

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
                target_stored_name="",
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
            {"target_stored_name": ""},
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

    def test_websocket_revision_aliases_are_compatible_and_conflicts_fail(self) -> None:
        self.assertIsNone(main.project_command_page_revision({}))
        self.assertEqual(
            main.project_command_page_revision({"expected_revision": 4}),
            4,
        )
        self.assertEqual(
            main.project_command_page_revision(
                {
                    "expected_revision": 4,
                    "expected_page_revision": 4,
                }
            ),
            4,
        )
        with self.assertRaises(ValueError):
            main.project_command_page_revision(
                {
                    "expected_revision": 3,
                    "expected_page_revision": 4,
                }
            )

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
