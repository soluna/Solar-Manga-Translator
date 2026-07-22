from __future__ import annotations

import asyncio
import ast
import copy
import inspect
import sys
import tempfile
import textwrap
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
    CorruptProjectArtifactError,
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


MISSING_PAGE_REVISION = object()


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
    *,
    page_revision: object = 4,
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
    page_metadata: dict[str, object] = {"document_version": 2}
    if page_revision is not MISSING_PAGE_REVISION:
        page_metadata["revision"] = page_revision
    page_document = {
        "page_id": "001.png",
        "regions": [],
        "metadata": page_metadata,
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


def durable_command_surfaces(
    workspace: ProjectWorkspace,
    project_id: str,
) -> dict[str, object]:
    def optional_bytes(path: Path) -> bytes | None:
        return path.read_bytes() if path.is_file() else None

    def tree_bytes(root: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        } if root.is_dir() else {}

    snapshots_dir = workspace.project_snapshots_dir(project_id)
    state_document = workspace.read_project_session_document(project_id) or {}
    projected_roots = {
        field: tree_bytes(Path(path))
        for field in ("source_dir", "translated_dir", "rerender_cache_dir")
        if isinstance((path := state_document.get(field)), str) and path
    }
    return {
        "head": optional_bytes(workspace.project_head_path(project_id)),
        "pending": optional_bytes(workspace.project_pending_artifact_path(project_id)),
        "snapshots": tree_bytes(snapshots_dir),
        "compatibility": {
            "state": optional_bytes(workspace.project_session_state_path(project_id)),
            "manifest": optional_bytes(workspace.project_manifest_path(project_id)),
            "pages": tree_bytes(workspace.project_pages_dir(project_id)),
            "artifact_roots": projected_roots,
            "archive": optional_bytes(
                workspace.project_temp_path(project_id, "result.zip")
            ),
            "index": optional_bytes(workspace.project_index_path),
        },
    }


class DeterministicRenderPageAdapter:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.calls = 0
        self.working_root: Path | None = None
        self.observed_source = b""

    def project_command_fingerprint(self, command):
        return repr(
            (
                command.action,
                command.target_stored_name,
                sorted(dict(command.config).items()),
            )
        )

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


class DeterministicProjectCommandAdapter(DeterministicRenderPageAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.project_actions: list[str] = []

    async def prepare_project_command(self, command, working_set, progress):
        self.project_actions.append(command.action)
        await progress({"event": "status", "message": "project command"})
        state_document = copy.deepcopy(working_set.base.state_document)
        state_document["workflow_stage"] = "translated"
        runtime_session = ProjectState.load(
            state_document,
            expected_project_id=working_set.base.project_id,
        ).to_runtime_session()
        return PreparedHeadUpdate(
            state_document=state_document,
            project_manifest={
                **working_set.base.project_manifest,
                "workflow_stage": "translated",
            },
            page_documents=copy.deepcopy(working_set.base.page_documents),
            artifact_files={},
            replace_prefixes=(),
            remove_logical_paths=set(),
            runtime_session=runtime_session,
            execution_extras={},
            snapshot_document={
                "kind": "deterministic_project_command",
                "summary": command.action,
                "workflow_stage": "translated",
            },
        )


class WorkflowCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    def test_legacy_workflow_facade_is_deleted(self) -> None:
        legacy_names = (
            "detect_" + "session",
            "translate_" + "session",
            "resume_translation_" + "session",
            "rerender_" + "session",
        )

        for legacy_name in legacy_names:
            with self.subTest(legacy_name=legacy_name):
                self.assertNotIn(legacy_name, vars(main.TranslatorEngine))
        self.assertNotIn("execute", vars(TranslatorEngineWorkflowAdapter))

    def make_render_page_coordinator(
        self,
        workspace: ProjectWorkspace,
        shared_project: dict[str, Any],
        render_adapter: DeterministicRenderPageAdapter,
    ) -> WorkflowCoordinator:
        return WorkflowCoordinator(
            project_loader=lambda _project_id: shared_project,
            volatile_execution_adapter=DeterministicExecutionAdapter(),
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
            preparation_adapter=render_adapter,
        )

    async def test_project_commands_use_head_bound_coordinator_execution(self) -> None:
        for action in ("rerender", "detect", "translate", "resume-translate"):
            with self.subTest(action=action):
                with tempfile.TemporaryDirectory() as tmp:
                    workspace, shared_project, first_head = seed_render_page_project(
                        Path(tmp)
                    )
                    adapter = DeterministicProjectCommandAdapter()
                    legacy_execution = mock.AsyncMock()
                    coordinator = WorkflowCoordinator(
                        project_loader=lambda _project_id: shared_project,
                        volatile_execution_adapter=legacy_execution,
                        project_view_builder=lambda project_id, project: {
                            "session_id": project_id,
                            "workflow_stage": project["workflow_stage"],
                            "project": {"is_busy": True, "busy_action": action},
                        },
                        project_workspace=workspace,
                        preparation_adapter=adapter,
                    )

                    result = await coordinator.execute(
                        ProjectCommand(
                            project_id="project-a",
                            action=action,
                            config={"target_lang": "CHS"},
                        )
                    )

                    self.assertEqual(adapter.project_actions, [action])
                    legacy_execution.execute.assert_not_awaited()
                    self.assertEqual(
                        workspace.read_project_head("project-a")["generation"],
                        first_head["generation"] + 1,
                    )
                    snapshots = workspace.read_snapshot_manifests("project-a")
                    self.assertEqual(len(snapshots), 1)
                    self.assertEqual(
                        snapshots[0]["project_head_revision_id"],
                        workspace.read_project_head("project-a")["revision_id"],
                    )
                    self.assertEqual(result["workflow_stage"], "translated")

    async def test_translate_page_rejects_invalid_stored_revision_without_durable_writes(
        self,
    ) -> None:
        cases = (
            ("missing", MISSING_PAGE_REVISION),
            ("true", True),
            ("false", False),
            ("string", "4"),
            ("float", 4.0),
            ("zero", 0),
            ("negative", -1),
        )
        for label, invalid_revision in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspace, shared_project, first_head = seed_render_page_project(
                    root,
                    page_revision=invalid_revision,
                )
                pending_output = root / "pending-output.png"
                pending_output.write_bytes(b"pending output")
                workspace.write_pending_artifact_set(
                    "project-a",
                    action="rerender",
                    resume_fingerprint="different-command",
                    base_head=first_head,
                    state_document=workspace.read_project_state_from_head(
                        "project-a",
                        first_head,
                    ),
                    files={"translated/001.png": pending_output},
                )
                workspace.create_project_head_snapshot(
                    "project-a",
                    first_head,
                    {"kind": "baseline"},
                )
                durable_before = durable_command_surfaces(
                    workspace,
                    "project-a",
                )
                adapter = DeterministicProjectCommandAdapter()
                legacy_execution = mock.AsyncMock()
                coordinator = WorkflowCoordinator(
                    project_loader=lambda _project_id: shared_project,
                    volatile_execution_adapter=legacy_execution,
                    project_view_builder=lambda _project_id, _project: {},
                    project_workspace=workspace,
                    preparation_adapter=adapter,
                )

                with self.assertRaises(CorruptProjectArtifactError):
                    await coordinator.execute(
                        ProjectCommand(
                            project_id="project-a",
                            action="translate-page",
                            config={},
                            target_stored_name="001.png",
                        )
                    )

                self.assertEqual(adapter.project_actions, [])
                legacy_execution.execute.assert_not_awaited()
                self.assertEqual(
                    durable_command_surfaces(workspace, "project-a"),
                    durable_before,
                )

    async def test_project_command_rejects_invalid_orphan_head_page_without_durable_writes(
        self,
    ) -> None:
        for action in ("detect", "translate"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspace, shared_project, first_head = seed_render_page_project(root)
                orphan_head = workspace.commit_project_head(
                    "project-a",
                    state_document=workspace.read_project_state_from_head(
                        "project-a",
                        first_head,
                    ),
                    project_manifest=workspace.read_project_manifest("project-a"),
                    page_documents={
                        "002.png": {
                            "page_id": "002.png",
                            "regions": [],
                            "metadata": {
                                "document_version": 2,
                                "revision": "bad",
                            },
                        }
                    },
                    expected_generation=first_head["generation"],
                    expected_revision_id=first_head["revision_id"],
                )
                pending_output = root / "pending-output.png"
                pending_output.write_bytes(b"pending output")
                workspace.write_pending_artifact_set(
                    "project-a",
                    action="rerender",
                    resume_fingerprint="different-command",
                    base_head=orphan_head,
                    state_document=workspace.read_project_state_from_head(
                        "project-a",
                        orphan_head,
                    ),
                    files={"translated/001.png": pending_output},
                )
                workspace.create_project_head_snapshot(
                    "project-a",
                    orphan_head,
                    {"kind": "baseline"},
                )
                durable_before = durable_command_surfaces(workspace, "project-a")
                adapter = DeterministicProjectCommandAdapter()
                legacy_execution = mock.AsyncMock()
                coordinator = WorkflowCoordinator(
                    project_loader=lambda _project_id: shared_project,
                    volatile_execution_adapter=legacy_execution,
                    project_view_builder=lambda _project_id, _project: {},
                    project_workspace=workspace,
                    preparation_adapter=adapter,
                )

                with self.assertRaises(CorruptProjectArtifactError):
                    await coordinator.execute(
                        ProjectCommand(
                            project_id="project-a",
                            action=action,
                            config={},
                        )
                    )

                self.assertEqual(adapter.project_actions, [])
                legacy_execution.execute.assert_not_awaited()
                self.assertEqual(
                    durable_command_surfaces(workspace, "project-a"),
                    durable_before,
                )

    async def test_render_page_rejects_invalid_head_inventory_without_durable_writes(
        self,
    ) -> None:
        for case in ("orphan-invalid-revision", "ambiguous-page-path"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspace, shared_project, first_head = seed_render_page_project(root)
                if case == "orphan-invalid-revision":
                    current_head = workspace.commit_project_head(
                        "project-a",
                        state_document=workspace.read_project_state_from_head(
                            "project-a",
                            first_head,
                        ),
                        project_manifest=workspace.read_project_manifest("project-a"),
                        page_documents={
                            "002.png": {
                                "page_id": "002.png",
                                "regions": [],
                                "metadata": {
                                    "document_version": 2,
                                    "revision": "bad",
                                },
                            }
                        },
                        expected_generation=first_head["generation"],
                        expected_revision_id=first_head["revision_id"],
                    )
                else:
                    workspace.create_project_head_snapshot(
                        "project-a",
                        first_head,
                        {"kind": "baseline"},
                    )
                    current_head = {
                        **first_head,
                        "files": {
                            **first_head["files"],
                            "pages//001.png/page_document.json": first_head["files"][
                                "pages/001.png/page_document.json"
                            ],
                        },
                    }
                    workspace.write_json_file(
                        workspace.project_head_path("project-a"),
                        current_head,
                    )
                pending_output = root / "pending-output.png"
                pending_output.write_bytes(b"pending output")
                workspace.write_pending_artifact_set(
                    "project-a",
                    action="rerender",
                    resume_fingerprint="different-command",
                    base_head=current_head,
                    state_document=workspace.read_project_state_from_head(
                        "project-a",
                        current_head,
                    ),
                    files={"translated/001.png": pending_output},
                )
                if case == "orphan-invalid-revision":
                    workspace.create_project_head_snapshot(
                        "project-a",
                        current_head,
                        {"kind": "baseline"},
                    )
                durable_before = durable_command_surfaces(workspace, "project-a")
                adapter = DeterministicRenderPageAdapter()
                legacy_execution = mock.AsyncMock()
                coordinator = WorkflowCoordinator(
                    project_loader=lambda _project_id: shared_project,
                    volatile_execution_adapter=legacy_execution,
                    project_view_builder=lambda _project_id, _project: {},
                    project_workspace=workspace,
                    preparation_adapter=adapter,
                )

                with self.assertRaises(CorruptProjectArtifactError):
                    await coordinator.execute(
                        ProjectCommand(
                            project_id="project-a",
                            action="rerender",
                            config={},
                            target_stored_name="001.png",
                            expected_page_revision=4,
                        )
                    )

                self.assertEqual(adapter.calls, 0)
                legacy_execution.execute.assert_not_awaited()
                self.assertEqual(
                    durable_command_surfaces(workspace, "project-a"),
                    durable_before,
                )

    async def test_corrupt_pending_fails_closed_before_project_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, shared_project, first_head = seed_render_page_project(
                Path(tmp)
            )
            workspace.project_pending_artifact_path("project-a").write_text(
                "{not-json",
                encoding="utf-8",
            )
            adapter = DeterministicProjectCommandAdapter()
            coordinator = WorkflowCoordinator(
                project_loader=lambda _project_id: shared_project,
                volatile_execution_adapter=DeterministicExecutionAdapter(),
                project_view_builder=lambda _project_id, _project: {},
                project_workspace=workspace,
                preparation_adapter=adapter,
            )

            with self.assertRaises(CorruptProjectArtifactError):
                await coordinator.execute(
                    ProjectCommand(
                        project_id="project-a",
                        action="translate",
                        config={},
                    )
                )

            self.assertEqual(adapter.project_actions, [])
            self.assertEqual(workspace.read_project_head("project-a"), first_head)

    async def test_pending_reuse_matches_base_generation_and_revision_independently(
        self,
    ) -> None:
        cases = (
            ("exact", None, True),
            ("generation", "base_head_generation", False),
            ("revision", "base_head_revision_id", False),
        )
        for label, changed_field, expected_restored in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspace, shared_project, first_head = seed_render_page_project(root)
                pending_output = root / "pending-output.png"
                pending_output.write_bytes(b"verified pending output")
                pending_state = workspace.read_project_state_from_head(
                    "project-a",
                    first_head,
                )
                pending_state["workflow_stage"] = "translating"
                workspace.write_pending_artifact_set(
                    "project-a",
                    action="translate",
                    resume_fingerprint="exact-command",
                    base_head=first_head,
                    state_document=pending_state,
                    files={"translated/001.png": pending_output},
                )
                pending_path = workspace.project_pending_artifact_path("project-a")
                if changed_field is not None:
                    pending = workspace.read_json_file(pending_path, {})
                    if changed_field == "base_head_generation":
                        pending[changed_field] = first_head["generation"] + 1
                    else:
                        pending[changed_field] = "another-valid-revision"
                    workspace.write_json_file(pending_path, pending)
                diagnostic_evidence = pending_path.read_bytes()
                base = workspace.read_project_command_base("project-a")

                with workspace.materialize_project_working_set(
                    base,
                    action="translate",
                    resume_fingerprint="exact-command",
                    legacy_project=shared_project,
                ) as working_set:
                    self.assertIs(
                        working_set.pending_restored,
                        expected_restored,
                    )

                self.assertEqual(pending_path.read_bytes(), diagnostic_evidence)

    async def test_markerless_corrupt_pending_fails_closed_even_when_nonmatching(
        self,
    ) -> None:
        cases = (
            ("matching-state", True, "state"),
            ("nonmatching-state", False, "state"),
            ("nonmatching-path", False, "path"),
            ("nonmatching-root-only", False, "root-only"),
            ("nonmatching-unknown-root", False, "unknown-root"),
        )
        for label, matching, corruption in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspace, shared_project, first_head = seed_render_page_project(root)
                adapter = DeterministicProjectCommandAdapter()
                command = ProjectCommand(
                    project_id="project-a",
                    action="translate",
                    config={},
                )
                pending_artifact = root / "pending-output.png"
                pending_artifact.write_bytes(b"verified pending output")
                workspace.write_pending_artifact_set(
                    "project-a",
                    action="rerender",
                    resume_fingerprint=(
                        adapter.project_command_fingerprint(command)
                        if matching
                        else "different-command-fingerprint"
                    ),
                    base_head=first_head,
                    state_document=workspace.read_project_state_from_head(
                        "project-a",
                        first_head,
                    ),
                    files={"translated/001.png": pending_artifact},
                )
                pending_path = workspace.project_pending_artifact_path("project-a")
                pending = workspace.read_json_file(pending_path, {})
                if matching:
                    pending["action"] = "translate"
                self.assertNotIn("state_validated", pending)
                if corruption == "state":
                    pending["state_document"] = {}
                elif corruption == "path":
                    files = pending["artifact_bundle"]["files"]
                    files["../escape.png"] = files.pop("translated/001.png")
                elif corruption == "root-only":
                    files = pending["artifact_bundle"]["files"]
                    files["translated"] = files.pop("translated/001.png")
                else:
                    files = pending["artifact_bundle"]["files"]
                    files["diagnostics/output.png"] = files.pop(
                        "translated/001.png"
                    )
                workspace.write_json_file(pending_path, pending)
                diagnostic_evidence = pending_path.read_bytes()
                coordinator = WorkflowCoordinator(
                    project_loader=lambda _project_id: shared_project,
                    volatile_execution_adapter=DeterministicExecutionAdapter(),
                    project_view_builder=lambda _project_id, _project: {},
                    project_workspace=workspace,
                    preparation_adapter=adapter,
                )

                with self.assertRaises(CorruptProjectArtifactError):
                    await coordinator.execute(command)

                self.assertEqual(adapter.project_actions, [])
                self.assertEqual(workspace.read_project_head("project-a"), first_head)
                self.assertEqual(pending_path.read_bytes(), diagnostic_evidence)

    async def test_empty_pending_still_validates_action_stage_before_nonmatching_execution(
        self,
    ) -> None:
        cases = (
            ("unknown-action", "future-action", "translated"),
            ("impossible-stage", "detect", "translated"),
        )
        for label, pending_action, pending_stage in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                workspace, shared_project, first_head = seed_render_page_project(root)
                adapter = DeterministicProjectCommandAdapter()
                command = ProjectCommand(
                    project_id="project-a",
                    action="translate",
                    config={},
                )
                pending_artifact = root / "pending-output.png"
                pending_artifact.write_bytes(b"verified pending output")
                pending_state = workspace.read_project_state_from_head(
                    "project-a",
                    first_head,
                )
                pending_state["workflow_stage"] = "translated"
                workspace.write_pending_artifact_set(
                    "project-a",
                    action="rerender",
                    resume_fingerprint="different-command-fingerprint",
                    base_head=first_head,
                    state_document=pending_state,
                    files={"translated/001.png": pending_artifact},
                    metadata={"page_checkpoints": {}},
                )
                pending_path = workspace.project_pending_artifact_path("project-a")
                corrupt_pending = workspace.read_json_file(pending_path, {})
                corrupt_pending["action"] = pending_action
                corrupt_pending["state_document"]["workflow_stage"] = pending_stage
                workspace.write_json_file(pending_path, corrupt_pending)
                diagnostic_evidence = pending_path.read_bytes()
                legacy_execution = mock.AsyncMock()
                coordinator = WorkflowCoordinator(
                    project_loader=lambda _project_id: shared_project,
                    volatile_execution_adapter=legacy_execution,
                    project_view_builder=lambda _project_id, _project: {},
                    project_workspace=workspace,
                    preparation_adapter=adapter,
                )

                with mock.patch.object(
                    workspace,
                    "restore_pending_artifact_set",
                    wraps=workspace.restore_pending_artifact_set,
                ) as restore_pending:
                    with self.assertRaises(CorruptProjectArtifactError):
                        await coordinator.execute(command)

                restore_pending.assert_not_called()
                self.assertEqual(adapter.project_actions, [])
                legacy_execution.execute.assert_not_awaited()
                self.assertEqual(workspace.read_project_head("project-a"), first_head)
                self.assertEqual(pending_path.read_bytes(), diagnostic_evidence)

    async def test_empty_pending_accepts_canonical_action_stage_matrix(self) -> None:
        allowed_pairs = (
            ("detect", "detecting"),
            ("detect", "detected"),
            ("translate", "detecting"),
            ("translate", "detected"),
            ("translate", "translating"),
            ("resume-translate", "translating"),
            ("translate-page", "translating"),
            ("rerender", "translated"),
        )
        for pending_action, pending_stage in allowed_pairs:
            with (
                self.subTest(action=pending_action, stage=pending_stage),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                workspace, _shared_project, first_head = seed_render_page_project(root)
                pending_artifact = root / "pending-output.png"
                pending_artifact.write_bytes(b"verified pending output")
                pending_state = workspace.read_project_state_from_head(
                    "project-a",
                    first_head,
                )
                pending_state["workflow_stage"] = pending_stage
                workspace.write_pending_artifact_set(
                    "project-a",
                    action=pending_action,
                    resume_fingerprint="canonical-empty-checkpoint",
                    base_head=first_head,
                    state_document=pending_state,
                    files={"translated/001.png": pending_artifact},
                    metadata={"page_checkpoints": {}},
                )

                pending = workspace.read_pending_artifact_set("project-a")

                self.assertEqual(pending["action"], pending_action)
                self.assertEqual(
                    pending["state_document"]["workflow_stage"],
                    pending_stage,
                )
                self.assertEqual(pending["page_checkpoints"], {})

    async def test_project_archive_stays_private_when_head_commit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, shared_project, first_head = seed_render_page_project(
                Path(tmp)
            )
            canonical_archive = workspace.project_temp_path(
                "project-a",
                "result.zip",
            )
            canonical_archive.parent.mkdir(parents=True, exist_ok=True)
            canonical_archive.write_bytes(b"old archive")

            class ArchivePreparingAdapter(DeterministicProjectCommandAdapter):
                async def prepare_project_command(self, command, working_set, progress):
                    prepared = await super().prepare_project_command(
                        command,
                        working_set,
                        progress,
                    )
                    private_archive = working_set.archive_dir / "result.zip"
                    private_archive.write_bytes(b"new archive")
                    return PreparedHeadUpdate(
                        state_document=prepared.state_document,
                        project_manifest=prepared.project_manifest,
                        page_documents=prepared.page_documents,
                        artifact_files={"archive/result.zip": private_archive},
                        replace_prefixes=("archive/",),
                        remove_logical_paths=set(),
                        runtime_session=prepared.runtime_session,
                        execution_extras=prepared.execution_extras,
                    )

            adapter = ArchivePreparingAdapter()
            coordinator = WorkflowCoordinator(
                project_loader=lambda _project_id: shared_project,
                volatile_execution_adapter=DeterministicExecutionAdapter(),
                project_view_builder=lambda _project_id, _project: {},
                project_workspace=workspace,
                preparation_adapter=adapter,
            )
            with mock.patch.object(
                workspace,
                "commit_project_working_set",
                side_effect=OSError("synthetic CAS failure"),
            ):
                with self.assertRaisesRegex(OSError, "CAS failure"):
                    await coordinator.execute(
                        ProjectCommand(
                            project_id="project-a",
                            action="translate",
                            config={},
                        )
                    )

            self.assertEqual(canonical_archive.read_bytes(), b"old archive")
            self.assertEqual(workspace.read_project_head("project-a"), first_head)

    async def test_project_command_cancellation_propagates_without_commit_or_fallback(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, shared_project, first_head = seed_render_page_project(
                Path(tmp)
            )
            cancellation = asyncio.CancelledError()

            class CancellingProjectAdapter(DeterministicProjectCommandAdapter):
                async def prepare_project_command(self, command, working_set, progress):
                    self.project_actions.append(command.action)
                    self.working_root = working_set.root
                    await progress({"event": "status", "message": "cancelling"})
                    raise cancellation

            adapter = CancellingProjectAdapter()
            legacy_execution = mock.AsyncMock()
            view_builder = mock.Mock()
            coordinator = WorkflowCoordinator(
                project_loader=lambda _project_id: shared_project,
                volatile_execution_adapter=legacy_execution,
                project_view_builder=view_builder,
                project_workspace=workspace,
                preparation_adapter=adapter,
            )

            with mock.patch.object(
                workspace,
                "commit_project_working_set",
                wraps=workspace.commit_project_working_set,
            ) as commit:
                with self.assertRaises(asyncio.CancelledError) as raised:
                    await coordinator.execute(
                        ProjectCommand(
                            project_id="project-a",
                            action="translate",
                            config={},
                        )
                    )

            self.assertIs(raised.exception, cancellation)
            self.assertEqual(adapter.project_actions, ["translate"])
            self.assertFalse(adapter.working_root.exists())
            self.assertEqual(workspace.read_project_head("project-a"), first_head)
            commit.assert_not_called()
            legacy_execution.execute.assert_not_awaited()
            view_builder.assert_not_called()

    async def test_translate_page_rejects_stale_revision_before_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, shared_project, first_head = seed_render_page_project(
                Path(tmp)
            )
            adapter = DeterministicProjectCommandAdapter()
            coordinator = WorkflowCoordinator(
                project_loader=lambda _project_id: shared_project,
                volatile_execution_adapter=DeterministicExecutionAdapter(),
                project_view_builder=lambda _project_id, _project: {},
                project_workspace=workspace,
                preparation_adapter=adapter,
            )

            with self.assertRaises(PageDocumentRevisionConflict):
                await coordinator.execute(
                    ProjectCommand(
                        project_id="project-a",
                        action="translate-page",
                        config={},
                        target_stored_name="001.png",
                        expected_page_revision=3,
                    )
                )

            self.assertEqual(adapter.project_actions, [])
            self.assertEqual(workspace.read_project_head("project-a"), first_head)

    async def test_project_progress_failure_cannot_change_committed_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, shared_project, first_head = seed_render_page_project(
                Path(tmp)
            )
            adapter = DeterministicProjectCommandAdapter()
            coordinator = WorkflowCoordinator(
                project_loader=lambda _project_id: shared_project,
                volatile_execution_adapter=DeterministicExecutionAdapter(),
                project_view_builder=lambda project_id, project: {
                    "session_id": project_id,
                    "workflow_stage": project["workflow_stage"],
                    "project": {},
                },
                project_workspace=workspace,
                preparation_adapter=adapter,
            )

            async def disconnected(_event):
                raise OSError("subscriber unavailable")

            result = await coordinator.execute(
                ProjectCommand(
                    project_id="project-a",
                    action="translate",
                    config={},
                ),
                progress=disconnected,
            )

            self.assertEqual(
                workspace.read_project_head("project-a")["generation"],
                first_head["generation"] + 1,
            )
            self.assertTrue(
                any("subscriber unavailable" in warning for warning in result["warnings"])
            )

    async def test_project_pending_cleanup_failure_after_cas_is_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, shared_project, first_head = seed_render_page_project(
                Path(tmp)
            )

            class CheckpointingProjectAdapter(DeterministicProjectCommandAdapter):
                async def prepare_project_command(
                    self,
                    command,
                    working_set,
                    progress,
                ):
                    prepared = await super().prepare_project_command(
                        command,
                        working_set,
                        progress,
                    )
                    pending_state = copy.deepcopy(
                        working_set.base.state_document
                    )
                    pending_state["workflow_stage"] = "translating"
                    workspace.write_pending_artifact_set(
                        working_set.base.project_id,
                        action=command.action,
                        resume_fingerprint=self.project_command_fingerprint(
                            command
                        ),
                        base_head=working_set.base.head,
                        state_document=pending_state,
                        files={},
                        metadata={"page_checkpoints": {}},
                    )
                    return prepared

            adapter = CheckpointingProjectAdapter()
            coordinator = WorkflowCoordinator(
                project_loader=lambda _project_id: shared_project,
                volatile_execution_adapter=DeterministicExecutionAdapter(),
                project_view_builder=lambda project_id, project: {
                    "session_id": project_id,
                    "workflow_stage": project["workflow_stage"],
                    "project": {},
                },
                project_workspace=workspace,
                preparation_adapter=adapter,
            )
            with mock.patch.object(
                workspace,
                "clear_pending_artifact_set",
                side_effect=OSError("pending cleanup unavailable"),
            ), mock.patch.object(
                workspace,
                "garbage_collect_snapshot_blobs",
                wraps=workspace.garbage_collect_snapshot_blobs,
            ) as collect:
                result = await coordinator.execute(
                    ProjectCommand(
                        project_id="project-a",
                        action="translate",
                        config={},
                    )
                )

            self.assertEqual(
                workspace.read_project_head("project-a")["generation"],
                first_head["generation"] + 1,
            )
            self.assertTrue(
                any(
                    "pending cleanup unavailable" in warning
                    for warning in result["warnings"]
                )
            )
            collect.assert_called_once()

    async def test_project_closeout_preserves_pending_owned_by_a_new_head_command(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace, shared_project, first_head = seed_render_page_project(root)
            adapter = DeterministicProjectCommandAdapter()
            coordinator = WorkflowCoordinator(
                project_loader=lambda _project_id: shared_project,
                volatile_execution_adapter=DeterministicExecutionAdapter(),
                project_view_builder=lambda project_id, project: {
                    "session_id": project_id,
                    "workflow_stage": project["workflow_stage"],
                    "project": {},
                },
                project_workspace=workspace,
                preparation_adapter=adapter,
            )
            original_refresh = workspace.refresh_project_index_entry
            concurrent_evidence: dict[str, object] = {}

            def refresh_and_start_concurrent_command(summary):
                result = original_refresh(summary)
                current_head = workspace.read_project_head("project-a")
                if (
                    not concurrent_evidence
                    and current_head is not None
                    and current_head["generation"] == first_head["generation"] + 1
                ):
                    concurrent_pending = workspace.write_pending_artifact_set(
                        "project-a",
                        action="rerender",
                        resume_fingerprint="concurrent-new-head-command",
                        base_head=current_head,
                        state_document=workspace.read_project_state_from_head(
                            "project-a",
                            current_head,
                        ),
                        files={},
                        metadata={"page_checkpoints": {}},
                    )
                    pending_path = workspace.project_pending_artifact_path(
                        "project-a"
                    )
                    concurrent_evidence.update(
                        {
                            "pending": concurrent_pending,
                            "bytes": pending_path.read_bytes(),
                        }
                    )
                return result

            with mock.patch.object(
                workspace,
                "refresh_project_index_entry",
                side_effect=refresh_and_start_concurrent_command,
            ):
                await coordinator.execute(
                    ProjectCommand(
                        project_id="project-a",
                        action="translate",
                        config={},
                    )
                )

            self.assertTrue(concurrent_evidence)
            pending_path = workspace.project_pending_artifact_path("project-a")
            self.assertTrue(pending_path.is_file())
            self.assertEqual(
                pending_path.read_bytes(),
                concurrent_evidence["bytes"],
            )
            pending = workspace.read_pending_artifact_set("project-a")
            self.assertEqual(pending["action"], "rerender")
            self.assertEqual(
                pending["resume_fingerprint"],
                "concurrent-new-head-command",
            )
            self.assertEqual(
                pending["base_head_generation"],
                first_head["generation"] + 1,
            )

    async def test_project_gc_failure_after_cas_is_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, shared_project, first_head = seed_render_page_project(
                Path(tmp)
            )
            adapter = DeterministicProjectCommandAdapter()
            coordinator = WorkflowCoordinator(
                project_loader=lambda _project_id: shared_project,
                volatile_execution_adapter=DeterministicExecutionAdapter(),
                project_view_builder=lambda project_id, project: {
                    "session_id": project_id,
                    "workflow_stage": project["workflow_stage"],
                    "project": {},
                },
                project_workspace=workspace,
                preparation_adapter=adapter,
            )
            with mock.patch.object(
                workspace,
                "garbage_collect_snapshot_blobs",
                side_effect=OSError("snapshot GC unavailable"),
            ):
                result = await coordinator.execute(
                    ProjectCommand(
                        project_id="project-a",
                        action="translate",
                        config={},
                    )
                )

            self.assertEqual(
                workspace.read_project_head("project-a")["generation"],
                first_head["generation"] + 1,
            )
            self.assertTrue(
                any(
                    "snapshot GC unavailable" in warning
                    for warning in result["warnings"]
                )
            )

    async def test_project_snapshot_create_failure_indexes_actual_empty_catalog(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, shared_project, first_head = seed_render_page_project(
                Path(tmp)
            )
            coordinator = WorkflowCoordinator(
                project_loader=lambda _project_id: shared_project,
                volatile_execution_adapter=DeterministicExecutionAdapter(),
                project_view_builder=lambda project_id, project: {
                    "session_id": project_id,
                    "workflow_stage": project["workflow_stage"],
                    "project": {},
                },
                project_workspace=workspace,
                preparation_adapter=DeterministicProjectCommandAdapter(),
            )
            with mock.patch.object(
                workspace,
                "create_project_head_snapshot",
                side_effect=OSError("snapshot create unavailable"),
            ), mock.patch.object(
                workspace,
                "enforce_snapshot_retention",
                wraps=workspace.enforce_snapshot_retention,
            ) as retention, mock.patch.object(
                workspace,
                "commit_project_head",
                wraps=workspace.commit_project_head,
            ) as commit:
                result = await coordinator.execute(
                    ProjectCommand(
                        project_id="project-a",
                        action="translate",
                        config={},
                    )
                )

            committed_head = workspace.read_project_head("project-a")
            self.assertEqual(committed_head["generation"], first_head["generation"] + 1)
            self.assertEqual(commit.call_count, 1)
            retention.assert_called_once_with("project-a")
            self.assertEqual(workspace.read_snapshot_manifests("project-a"), [])
            index_entry = workspace.read_json_file(workspace.project_index_path, [])[0]
            self.assertEqual(index_entry["snapshot_count"], 0)
            self.assertEqual(index_entry["latest_snapshot_id"], "")
            self.assertEqual(index_entry["latest_snapshot_kind"], "")
            self.assertEqual(index_entry["latest_snapshot_summary"], "")
            self.assertTrue(
                any("snapshot create unavailable" in warning for warning in result["warnings"])
            )

    async def test_project_snapshot_success_listing_and_rebuild_share_catalog_summary(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, shared_project, first_head = seed_render_page_project(Path(tmp))
            coordinator = WorkflowCoordinator(
                project_loader=lambda _project_id: shared_project,
                volatile_execution_adapter=DeterministicExecutionAdapter(),
                project_view_builder=lambda project_id, project: {
                    "session_id": project_id,
                    "workflow_stage": project["workflow_stage"],
                    "project": {},
                },
                project_workspace=workspace,
                preparation_adapter=DeterministicProjectCommandAdapter(),
            )

            await coordinator.execute(
                ProjectCommand(project_id="project-a", action="translate", config={})
            )

            committed_head = workspace.read_project_head("project-a")
            self.assertEqual(committed_head["generation"], first_head["generation"] + 1)
            snapshots = workspace.read_snapshot_manifests("project-a")
            self.assertEqual(len(snapshots), 1)
            index_entry = workspace.read_json_file(workspace.project_index_path, [])[0]
            self.assertEqual(index_entry["snapshot_count"], len(snapshots))
            self.assertEqual(index_entry["latest_snapshot_id"], snapshots[0]["snapshot_id"])
            self.assertEqual(index_entry["latest_snapshot_kind"], snapshots[0]["kind"])
            self.assertEqual(index_entry["latest_snapshot_summary"], snapshots[0]["summary"])

            workspace.project_index_path.unlink()
            rebuilt = workspace.rebuild_project_index()

            self.assertEqual(rebuilt[0], index_entry)

    async def test_project_post_cas_snapshot_pipeline_has_one_head_commit_and_fixed_order(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, shared_project, first_head = seed_render_page_project(Path(tmp))
            coordinator = WorkflowCoordinator(
                project_loader=lambda _project_id: shared_project,
                volatile_execution_adapter=DeterministicExecutionAdapter(),
                project_view_builder=lambda project_id, project: {
                    "session_id": project_id,
                    "workflow_stage": project["workflow_stage"],
                    "project": {},
                },
                project_workspace=workspace,
                preparation_adapter=DeterministicProjectCommandAdapter(),
            )
            events: list[str] = []
            original_create = workspace.create_project_head_snapshot
            original_catalog = workspace.read_snapshot_manifests
            original_refresh = workspace.refresh_project_index_entry

            def create(*args, **kwargs):
                events.append("create")
                return original_create(*args, **kwargs)

            def retention(*_args, **_kwargs):
                events.append("retention")

            def catalog(*args, **kwargs):
                events.append("catalog")
                return original_catalog(*args, **kwargs)

            def refresh(*args, **kwargs):
                events.append("index")
                return original_refresh(*args, **kwargs)

            def collect(*_args, **_kwargs):
                events.append("gc")

            with mock.patch.object(
                workspace,
                "commit_project_head",
                wraps=workspace.commit_project_head,
            ) as commit, mock.patch.object(
                workspace,
                "create_project_head_snapshot",
                side_effect=create,
            ), mock.patch.object(
                workspace,
                "enforce_snapshot_retention",
                side_effect=retention,
            ), mock.patch.object(
                workspace,
                "read_snapshot_manifests",
                side_effect=catalog,
            ), mock.patch.object(
                workspace,
                "refresh_project_index_entry",
                side_effect=refresh,
            ), mock.patch.object(
                workspace,
                "garbage_collect_snapshot_blobs",
                side_effect=collect,
            ):
                await coordinator.execute(
                    ProjectCommand(project_id="project-a", action="translate", config={})
                )

            self.assertEqual(events, ["create", "retention", "catalog", "index", "gc"])
            self.assertEqual(commit.call_count, 1)
            self.assertEqual(
                workspace.read_project_head("project-a")["generation"],
                first_head["generation"] + 1,
            )

    async def test_project_index_failure_after_snapshot_is_warning_and_does_not_block_gc(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, shared_project, first_head = seed_render_page_project(Path(tmp))
            coordinator = WorkflowCoordinator(
                project_loader=lambda _project_id: shared_project,
                volatile_execution_adapter=DeterministicExecutionAdapter(),
                project_view_builder=lambda project_id, project: {
                    "session_id": project_id,
                    "workflow_stage": project["workflow_stage"],
                    "project": {},
                },
                project_workspace=workspace,
                preparation_adapter=DeterministicProjectCommandAdapter(),
            )

            with mock.patch.object(
                workspace,
                "refresh_project_index_entry",
                side_effect=OSError("project index unavailable"),
            ), mock.patch.object(
                workspace,
                "garbage_collect_snapshot_blobs",
                wraps=workspace.garbage_collect_snapshot_blobs,
            ) as collect:
                result = await coordinator.execute(
                    ProjectCommand(project_id="project-a", action="translate", config={})
                )

            self.assertEqual(
                workspace.read_project_head("project-a")["generation"],
                first_head["generation"] + 1,
            )
            self.assertEqual(len(workspace.read_snapshot_manifests("project-a")), 1)
            self.assertTrue(
                any("project index unavailable" in warning for warning in result["warnings"])
            )
            collect.assert_called_once()

    async def test_project_snapshot_retention_failure_after_cas_is_warning(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace, shared_project, first_head = seed_render_page_project(
                Path(tmp)
            )
            snapshots_dir = workspace.project_snapshots_dir("project-a")
            snapshots_dir.mkdir(parents=True)
            for index in range(20):
                snapshot_id = f"legacy-{index:02d}"
                workspace.write_json_file(
                    snapshots_dir / f"{snapshot_id}.json",
                    {
                        "snapshot_id": snapshot_id,
                        "created_at": f"2026-06-{index + 1:02d}T00:00:00+00:00",
                        "kind": "legacy",
                        "summary": "legacy snapshot",
                    },
                )
            adapter = DeterministicProjectCommandAdapter()
            coordinator = WorkflowCoordinator(
                project_loader=lambda _project_id: shared_project,
                volatile_execution_adapter=DeterministicExecutionAdapter(),
                project_view_builder=lambda project_id, project: {
                    "session_id": project_id,
                    "workflow_stage": project["workflow_stage"],
                    "project": {},
                },
                project_workspace=workspace,
                preparation_adapter=adapter,
            )
            with mock.patch.object(
                workspace,
                "enforce_snapshot_retention",
                side_effect=OSError("snapshot retention unavailable"),
            ):
                result = await coordinator.execute(
                    ProjectCommand(
                        project_id="project-a",
                        action="translate",
                        config={},
                    )
                )

            committed_head = workspace.read_project_head("project-a")
            self.assertEqual(
                committed_head["generation"],
                first_head["generation"] + 1,
            )
            snapshots = workspace.read_snapshot_manifests("project-a")
            self.assertEqual(len(snapshots), 21)
            self.assertEqual(
                snapshots[0]["project_head_revision_id"],
                committed_head["revision_id"],
            )
            index_entry = workspace.read_json_file(workspace.project_index_path, [])[0]
            self.assertEqual(index_entry["snapshot_count"], 21)
            self.assertEqual(
                index_entry["latest_snapshot_id"],
                snapshots[0]["snapshot_id"],
            )
            self.assertTrue(
                any(
                    "snapshot retention unavailable" in warning
                    for warning in result["warnings"]
                )
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
                volatile_execution_adapter=DeterministicExecutionAdapter(),
                project_view_builder=fail_view,
                project_workspace=workspace,
                preparation_adapter=render_adapter,
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
            volatile_execution_adapter=ProgressAdapter(),
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
                    volatile_execution_adapter=RaisingAdapter(error),
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

    async def test_production_adapter_only_delegates_head_bound_preparation(
        self,
    ) -> None:
        engine = mock.Mock()
        prepared = object()
        engine.prepare_project_command_working_set = mock.AsyncMock(
            return_value=prepared
        )
        adapter = TranslatorEngineWorkflowAdapter(engine)

        async def ignore_progress(_event: dict[str, Any]) -> None:
            return None

        command = ProjectCommand(
            project_id="project-a",
            action="translate",
            config={"target_lang": "CHS"},
        )
        working_set = object()

        result = await adapter.prepare_project_command(
            command,
            working_set,
            ignore_progress,
        )

        self.assertIs(result, prepared)
        engine.prepare_project_command_working_set.assert_awaited_once_with(
            command=command,
            working_set=working_set,
            progress_callback=ignore_progress,
        )
        self.assertNotIn("execute", vars(type(adapter)))

    def test_main_assembly_injects_one_backend_and_provider_identity(self) -> None:
        inference_backend = object()
        translation_provider = object()
        engine = mock.Mock()
        with (
            mock.patch.object(
                main,
                "UpstreamInferenceBackend",
                return_value=inference_backend,
            ) as inference_constructor,
            mock.patch.object(
                main,
                "UpstreamTranslationProvider",
                return_value=translation_provider,
            ) as provider_constructor,
            mock.patch.object(
                main,
                "TranslatorEngine",
                return_value=engine,
            ) as engine_constructor,
        ):
            assembled = main.assemble_workflow_engine(
                main.BASE_DIR,
                main.APP_PATHS,
            )

        self.assertEqual(
            assembled,
            (inference_backend, translation_provider, engine),
        )
        inference_constructor.assert_called_once_with(main.BASE_DIR)
        provider_constructor.assert_called_once_with(inference_backend)
        engine_constructor.assert_called_once_with(
            main.BASE_DIR,
            app_paths=main.APP_PATHS,
            inference_backend=inference_backend,
            translation_provider=translation_provider,
        )
        self.assertIs(main.translator_engine.inference_backend, main.inference_backend)
        self.assertIs(
            main.translator_engine.translation_provider,
            main.translation_provider,
        )
        self.assertIs(main.workflow_preparation_adapter._engine, main.translator_engine)

    async def test_production_workspace_dispatch_never_calls_legacy_methods(
        self,
    ) -> None:
        for action, target in (
            ("rerender", None),
            ("detect", None),
            ("translate", None),
            ("resume-translate", None),
            ("translate-page", "001.png"),
        ):
            with self.subTest(action=action):
                with tempfile.TemporaryDirectory() as tmp:
                    workspace, shared_project, first_head = seed_render_page_project(
                        Path(tmp)
                    )
                    deterministic = DeterministicProjectCommandAdapter()
                    engine = mock.Mock()
                    engine.project_command_fingerprint.side_effect = lambda **fields: repr(
                        (
                            fields["action"],
                            fields["target_stored_name"],
                            sorted(fields["raw_config"].items()),
                        )
                    )

                    async def prepare_from_engine(
                        *, command, working_set, progress_callback
                    ):
                        return await deterministic.prepare_project_command(
                            command,
                            working_set,
                            progress_callback,
                        )

                    engine.prepare_project_command_working_set = mock.AsyncMock(
                        side_effect=prepare_from_engine
                    )
                    adapter = TranslatorEngineWorkflowAdapter(engine)
                    coordinator = WorkflowCoordinator(
                        project_loader=lambda _project_id: shared_project,
                        project_view_builder=lambda project_id, project: {
                            "session_id": project_id,
                            "workflow_stage": project["workflow_stage"],
                            "project": {"is_busy": True, "busy_action": action},
                        },
                        project_workspace=workspace,
                        preparation_adapter=adapter,
                    )

                    result = await coordinator.execute(
                        ProjectCommand(
                            project_id="project-a",
                            action=action,
                            config={"target_lang": "CHS"},
                            target_stored_name=target,
                            expected_page_revision=4 if target else None,
                        )
                    )

                    engine.prepare_project_command_working_set.assert_awaited_once()
                    self.assertEqual(
                        workspace.read_project_head("project-a")["generation"],
                        first_head["generation"] + 1,
                    )
                    self.assertEqual(result["workflow_stage"], "translated")

    def test_phase_a_cutover_keeps_shallow_modules_free_of_adapter_knowledge(
        self,
    ) -> None:
        coordinator_source = inspect.getsource(sys.modules["workflow_coordinator"])
        main_source = inspect.getsource(main)
        production_preparation = inspect.getsource(
            main.TranslatorEngine.prepare_project_command_working_set
        )

        for forbidden in (
            "create_subprocess_exec",
            "manga_translator",
            "provider_name ==",
            "selected_translator ==",
            "CUSTOM_OPENAI_API_KEY",
            "GEMINI_API_KEY",
        ):
            with self.subTest(module="workflow_coordinator", forbidden=forbidden):
                self.assertNotIn(forbidden, coordinator_source)
        for forbidden in (
            "provider_name ==",
            "selected_translator ==",
            "create_subprocess_exec",
        ):
            with self.subTest(module="main", forbidden=forbidden):
                self.assertNotIn(forbidden, main_source)
        production_calls = {
            node.func.attr
            for node in ast.walk(
                ast.parse(textwrap.dedent(production_preparation))
            )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        legacy_names = (
            "detect_" + "session",
            "translate_" + "session",
            "resume_translation_" + "session",
            "rerender_" + "session",
        )
        for legacy_name in legacy_names:
            with self.subTest(legacy_name=legacy_name):
                self.assertNotIn(legacy_name, production_calls)

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
            volatile_execution_adapter=DeterministicExecutionAdapter(),
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
            volatile_execution_adapter=DeterministicExecutionAdapter(),
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
