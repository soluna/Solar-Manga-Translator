from __future__ import annotations

import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from engine.project_workspace import (
    CorruptProjectArtifactError,
    CorruptSnapshotArtifactError,
    InvalidStorageIdentifierError,
    PreparedHeadUpdate,
    ProjectHeadConflictError,
    ProjectWorkspace,
)
from domain.project_artifacts import PageArtifactEvent, ProjectArtifactState
from runtime_paths import AppPaths


def make_test_paths(root: Path) -> AppPaths:
    return AppPaths(
        code_dir=BACKEND_DIR,
        app_data_dir=root / "app-data",
        models_dir=root / "models",
        output_dir=root / "output",
        logs_dir=root / "logs",
        cache_dir=root / "cache",
        config_dir=root / "config",
    )


class ProjectWorkspaceTests(unittest.TestCase):
    def make_workspace(self, root: Path) -> ProjectWorkspace:
        paths = make_test_paths(root)
        paths.ensure_directories()
        return ProjectWorkspace(paths)

    def snapshot_catalog_bytes(
        self,
        workspace: ProjectWorkspace,
        project_id: str,
    ) -> dict[str, bytes]:
        snapshots_dir = workspace.project_snapshots_dir(project_id)
        if not snapshots_dir.exists():
            return {}
        return {
            path.name: path.read_bytes()
            for path in sorted(snapshots_dir.iterdir())
            if path.is_file()
        }

    def file_tree_bytes(self, root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }

    def make_pending_checkpoint_evidence(
        self,
        root: Path,
        *,
        project_id: str = "project-a",
        page_id: str = "001.png",
    ) -> tuple[dict[str, object], dict[str, Path]]:
        cache_dir = root / "checkpoint-cache" / page_id
        translated_path = root / "checkpoint-translated" / page_id
        cache_dir.mkdir(parents=True)
        translated_path.parent.mkdir(parents=True)
        (cache_dir / "regions.json").write_text("[]", encoding="utf-8")
        (cache_dir / "meta.json").write_text(
            json.dumps(
                {
                    "base_kind": "inpainted",
                    "inpainting_region_count": 0,
                }
            ),
            encoding="utf-8",
        )
        Image.new("RGB", (8, 8), (255, 255, 255)).save(
            cache_dir / "inpainted.png"
        )
        Image.new("RGB", (8, 8), (10, 20, 30)).save(translated_path)
        artifact_state = (
            ProjectArtifactState.create([page_id])
            .apply(page_id, PageArtifactEvent.RECOGNIZED)
            .apply(page_id, PageArtifactEvent.TRANSLATED)
        )
        state_document: dict[str, object] = {
            "schema_version": 2,
            "project_id": project_id,
            "source_images": [{"name": page_id, "stored_name": page_id}],
            "translated_output_map": {page_id: page_id},
            "workflow_stage": "translating",
            "artifact_state": artifact_state.model_dump(mode="json"),
        }
        files = {
            f"cache/{page_id}/regions.json": cache_dir / "regions.json",
            f"cache/{page_id}/meta.json": cache_dir / "meta.json",
            f"cache/{page_id}/inpainted.png": cache_dir / "inpainted.png",
            f"translated/{page_id}": translated_path,
        }
        return state_document, files

    def test_project_and_page_paths_reject_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))

            for invalid_project_id in ("", ".", "..", "../outside", "nested/project", "nested\\project", "\x00"):
                with self.subTest(project_id=repr(invalid_project_id)):
                    with self.assertRaises(InvalidStorageIdentifierError):
                        workspace.project_dir(invalid_project_id)

            for invalid_page_id in ("", ".", "..", "../page.png", "nested/page.png", "nested\\page.png", "\x00"):
                with self.subTest(page_id=repr(invalid_page_id)):
                    with self.assertRaises(InvalidStorageIdentifierError):
                        workspace.project_page_document_path("project-a", invalid_page_id)

            self.assertEqual(
                workspace.project_page_document_path("project-a", "0001.png"),
                workspace.projects_root.resolve() / "project-a" / "pages" / "0001.png" / "page_document.json",
            )

    def test_command_base_reads_reject_every_invalid_stored_page_revision(self) -> None:
        missing = object()
        cases = (
            ("missing", missing),
            ("true", True),
            ("false", False),
            ("string", "4"),
            ("float", 4.0),
            ("zero", 0),
            ("negative", -1),
        )
        for label, invalid_revision in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                workspace = self.make_workspace(Path(tmp))
                metadata: dict[str, object] = {"document_version": 2}
                if invalid_revision is not missing:
                    metadata["revision"] = invalid_revision
                workspace.commit_project_head(
                    "project-a",
                    state_document={
                        "schema_version": 2,
                        "project_id": "project-a",
                        "source_images": [
                            {"name": "001.png", "stored_name": "001.png"}
                        ],
                    },
                    project_manifest={
                        "project_id": "project-a",
                        "title": "Project A",
                    },
                    page_documents={
                        "001.png": {
                            "page_id": "001.png",
                            "metadata": metadata,
                        }
                    },
                )

                with self.assertRaises(CorruptProjectArtifactError):
                    workspace.read_command_base("project-a", "001.png")
                with self.assertRaises(CorruptProjectArtifactError):
                    workspace.read_project_command_base("project-a")

    def test_project_command_base_validates_head_inventory_but_returns_canonical_pages(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            workspace.commit_project_head(
                "project-a",
                state_document={
                    "schema_version": 2,
                    "project_id": "project-a",
                    "source_images": [
                        {"name": "001.png", "stored_name": "001.png"}
                    ],
                },
                project_manifest={
                    "project_id": "project-a",
                    "title": "Project A",
                },
                page_documents={
                    "001.png": {
                        "page_id": "001.png",
                        "metadata": {"revision": 1},
                    },
                    "002.png": {
                        "page_id": "002.png",
                        "metadata": {"revision": 7},
                    },
                },
            )

            base = workspace.read_project_command_base("project-a")

            self.assertEqual(list(base.page_documents), ["001.png"])
            self.assertEqual(base.page_documents["001.png"]["metadata"]["revision"], 1)

    def test_page_command_base_rejects_invalid_orphan_head_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            workspace.commit_project_head(
                "project-a",
                state_document={
                    "schema_version": 2,
                    "project_id": "project-a",
                    "source_images": [
                        {"name": "001.png", "stored_name": "001.png"}
                    ],
                },
                project_manifest={
                    "project_id": "project-a",
                    "title": "Project A",
                },
                page_documents={
                    "001.png": {
                        "page_id": "001.png",
                        "metadata": {"revision": 1},
                    },
                    "002.png": {
                        "page_id": "002.png",
                        "metadata": {"revision": "bad"},
                    },
                },
            )

            with self.assertRaises(CorruptProjectArtifactError):
                workspace.read_command_base("project-a", "001.png")

    def test_project_command_base_rejects_ambiguous_head_logical_paths(self) -> None:
        cases = (
            "pages",
            "pages/001.png/extra.json",
            "pages/../001.png/page_document.json",
            "pages//001.png/page_document.json",
            "pages\\001.png\\page_document.json",
            "source//001.png",
        )
        for invalid_path in cases:
            with self.subTest(path=invalid_path), tempfile.TemporaryDirectory() as tmp:
                workspace = self.make_workspace(Path(tmp))
                head = workspace.commit_project_head(
                    "project-a",
                    state_document={
                        "schema_version": 2,
                        "project_id": "project-a",
                        "source_images": [
                            {"name": "001.png", "stored_name": "001.png"}
                        ],
                    },
                    project_manifest={
                        "project_id": "project-a",
                        "title": "Project A",
                    },
                    page_documents={
                        "001.png": {
                            "page_id": "001.png",
                            "metadata": {"revision": 1},
                        }
                    },
                )
                page_metadata = head["files"][
                    "pages/001.png/page_document.json"
                ]
                corrupt_head = {
                    **head,
                    "files": {
                        **head["files"],
                        invalid_path: page_metadata,
                    },
                }
                workspace.write_json_file(
                    workspace.project_head_path("project-a"),
                    corrupt_head,
                )

                with self.assertRaises(CorruptProjectArtifactError):
                    workspace.read_command_base("project-a", "001.png")
                with self.assertRaises(CorruptProjectArtifactError):
                    workspace.read_project_command_base("project-a")

    def test_legacy_project_command_base_uses_canonical_compatibility_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            workspace.write_json_file(
                workspace.project_session_state_path("project-a"),
                {
                    "schema_version": 2,
                    "project_id": "project-a",
                    "source_images": [
                        {"name": "001.png", "stored_name": "001.png"}
                    ],
                },
            )
            workspace.write_json_file(
                workspace.project_manifest_path("project-a"),
                {"project_id": "project-a", "title": "Legacy Project"},
            )
            workspace.write_json_file(
                workspace.project_page_document_path("project-a", "001.png"),
                {
                    "page_id": "001.png",
                    "metadata": {"revision": 3},
                },
            )
            workspace.write_json_file(
                workspace.project_page_document_path("project-a", "orphan.png"),
                {
                    "page_id": "orphan.png",
                    "metadata": {"revision": "legacy-residue"},
                },
            )

            base = workspace.read_project_command_base("project-a")

            self.assertIsNone(base.head)
            self.assertEqual(list(base.page_documents), ["001.png"])
            self.assertEqual(base.page_documents["001.png"]["metadata"]["revision"], 3)

    def test_legacy_project_command_base_rejects_invalid_referenced_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            workspace.write_json_file(
                workspace.project_session_state_path("project-a"),
                {
                    "schema_version": 2,
                    "project_id": "project-a",
                    "source_images": [
                        {"name": "001.png", "stored_name": "001.png"}
                    ],
                },
            )
            workspace.write_json_file(
                workspace.project_manifest_path("project-a"),
                {"project_id": "project-a", "title": "Legacy Project"},
            )
            workspace.write_json_file(
                workspace.project_page_document_path("project-a", "001.png"),
                {
                    "page_id": "001.png",
                    "metadata": {"revision": "bad"},
                },
            )

            with self.assertRaises(CorruptProjectArtifactError):
                workspace.read_project_command_base("project-a")

    def test_project_head_reuses_unchanged_page_revisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            project_id = "project-a"
            project_manifest = {
                "project_id": project_id,
                "title": "Project A",
                "updated_at": "2026-07-15T00:00:00+00:00",
            }
            state_document = {
                "schema_version": 2,
                "project_id": project_id,
                "source_images": [],
            }

            first_head = workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest=project_manifest,
                page_documents={
                    "001.png": {"page_id": "001.png", "metadata": {"revision": 1}},
                    "002.png": {"page_id": "002.png", "metadata": {"revision": 1}},
                },
            )
            second_head = workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest={**project_manifest, "updated_at": "2026-07-15T00:01:00+00:00"},
                page_documents={
                    "001.png": {"page_id": "001.png", "metadata": {"revision": 2}},
                },
            )

            self.assertEqual(first_head["generation"], 1)
            self.assertEqual(second_head["generation"], 2)
            self.assertNotEqual(
                first_head["files"]["pages/001.png/page_document.json"]["blob"],
                second_head["files"]["pages/001.png/page_document.json"]["blob"],
            )
            self.assertEqual(
                first_head["files"]["pages/002.png/page_document.json"]["blob"],
                second_head["files"]["pages/002.png/page_document.json"]["blob"],
            )
            self.assertEqual(
                workspace.read_project_page_document(project_id, "001.png")["metadata"]["revision"],
                2,
            )
            self.assertEqual(
                workspace.read_project_page_document(project_id, "002.png")["metadata"]["revision"],
                1,
            )

    def test_project_head_replaces_stale_artifact_paths_in_the_commit_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.make_workspace(root)
            project_id = "project-a"
            old_cache = root / "old-cache.json"
            old_output = root / "old-output.png"
            new_cache = root / "new-cache.json"
            new_output = root / "new-output.png"
            old_cache.write_text("old cache", encoding="utf-8")
            old_output.write_bytes(b"old output")
            new_cache.write_text("new cache", encoding="utf-8")
            new_output.write_bytes(b"new output")
            state_document = {"schema_version": 2, "project_id": project_id}
            project_manifest = {"project_id": project_id, "title": "Project A"}
            first_head = workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest=project_manifest,
                page_documents={"001.png": {"page_id": "001.png", "metadata": {"revision": 1}}},
                artifact_files={
                    "cache/001.png/obsolete.json": old_cache,
                    "translated/001-old.png": old_output,
                },
            )

            second_head = workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest=project_manifest,
                page_documents={"001.png": {"page_id": "001.png", "metadata": {"revision": 2}}},
                artifact_files={
                    "cache/001.png/current.json": new_cache,
                    "translated/001-new.png": new_output,
                },
                expected_generation=first_head["generation"],
                replace_prefixes=("cache/001.png/",),
                remove_logical_paths={"translated/001-old.png"},
            )

            self.assertNotIn("cache/001.png/obsolete.json", second_head["files"])
            self.assertNotIn("translated/001-old.png", second_head["files"])
            self.assertIn("cache/001.png/current.json", second_head["files"])
            self.assertIn("translated/001-new.png", second_head["files"])

    def test_project_head_rejects_a_stale_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            project_id = "project-a"
            state_document = {"schema_version": 2, "project_id": project_id}
            project_manifest = {"project_id": project_id, "title": "Project A"}
            first_head = workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest=project_manifest,
                page_documents={"001.png": {"page_id": "001.png", "metadata": {"revision": 1}}},
                expected_generation=0,
            )

            with self.assertRaises(ProjectHeadConflictError):
                workspace.commit_project_head(
                    project_id,
                    state_document=state_document,
                    project_manifest=project_manifest,
                    page_documents={"001.png": {"page_id": "001.png", "metadata": {"revision": 2}}},
                    expected_generation=0,
                )

            self.assertEqual(workspace.read_project_head(project_id), first_head)
            self.assertEqual(
                workspace.read_project_page_document(project_id, "001.png")["metadata"]["revision"],
                1,
            )

    def test_project_head_pointer_failure_preserves_the_previous_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            project_id = "project-a"
            state_document = {"schema_version": 2, "project_id": project_id}
            project_manifest = {"project_id": project_id, "title": "Project A"}
            first_head = workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest=project_manifest,
                page_documents={"001.png": {"page_id": "001.png", "metadata": {"revision": 1}}},
            )
            original_write = workspace.write_json_file

            def fail_only_at_head_pointer(path: Path, payload: object) -> None:
                if path == workspace.project_head_path(project_id):
                    raise OSError("simulated head pointer failure")
                original_write(path, payload)

            with mock.patch.object(
                workspace,
                "write_json_file",
                side_effect=fail_only_at_head_pointer,
            ):
                with self.assertRaisesRegex(OSError, "head pointer failure"):
                    workspace.commit_project_head(
                        project_id,
                        state_document=state_document,
                        project_manifest=project_manifest,
                        page_documents={"001.png": {"page_id": "001.png", "metadata": {"revision": 2}}},
                        expected_generation=1,
                    )

            self.assertEqual(workspace.read_project_head(project_id), first_head)
            self.assertEqual(
                workspace.read_project_page_document(project_id, "001.png")["metadata"]["revision"],
                1,
            )

    def test_project_head_rechecks_generation_and_revision_immediately_before_swap(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.make_workspace(root)
            project_id = "project-a"
            state_document = {"schema_version": 2, "project_id": project_id}
            project_manifest = {"project_id": project_id, "title": "Project A"}
            first_head = workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest=project_manifest,
                page_documents={
                    "001.png": {"page_id": "001.png", "metadata": {"revision": 1}}
                },
            )
            original_capture = workspace.capture_snapshot_artifacts
            concurrent_head = {
                **first_head,
                "generation": 2,
                "revision_id": "g00000002-concurrent",
            }
            advanced = False

            def capture_then_advance(*args, **kwargs):
                nonlocal advanced
                captured = original_capture(*args, **kwargs)
                if not advanced:
                    advanced = True
                    workspace.write_json_file(
                        workspace.project_revisions_dir(project_id)
                        / f"{concurrent_head['revision_id']}.json",
                        concurrent_head,
                    )
                    workspace.write_json_file(
                        workspace.project_head_path(project_id),
                        concurrent_head,
                    )
                return captured

            with mock.patch.object(
                workspace,
                "capture_snapshot_artifacts",
                side_effect=capture_then_advance,
            ):
                with self.assertRaises(ProjectHeadConflictError) as raised:
                    workspace.commit_project_head(
                        project_id,
                        state_document=state_document,
                        project_manifest=project_manifest,
                        page_documents={
                            "001.png": {
                                "page_id": "001.png",
                                "metadata": {"revision": 2},
                            }
                        },
                        expected_generation=first_head["generation"],
                        expected_revision_id=first_head["revision_id"],
                    )

            self.assertEqual(raised.exception.actual_generation, 2)
            self.assertEqual(workspace.read_project_head(project_id), concurrent_head)

    def test_compatibility_projection_failure_does_not_uncommit_the_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            project_id = "project-a"
            state_document = {"schema_version": 2, "project_id": project_id}
            project_manifest = {"project_id": project_id, "title": "Project A"}
            original_write = workspace.write_json_file

            def fail_only_at_session_projection(path: Path, payload: object) -> None:
                if path == workspace.project_session_state_path(project_id):
                    raise OSError("simulated compatibility projection failure")
                original_write(path, payload)

            with mock.patch.object(
                workspace,
                "write_json_file",
                side_effect=fail_only_at_session_projection,
            ):
                warnings: list[str] = []
                committed_head = workspace.commit_project_head(
                    project_id,
                    state_document=state_document,
                    project_manifest=project_manifest,
                    page_documents={"001.png": {"page_id": "001.png", "metadata": {"revision": 1}}},
                    warning_sink=warnings,
                )

            self.assertEqual(workspace.read_project_head(project_id), committed_head)
            self.assertEqual(
                workspace.read_project_session_document(project_id),
                state_document,
            )
            self.assertFalse(workspace.project_session_state_path(project_id).exists())
            self.assertEqual(len(warnings), 1)
            self.assertIn("state/session.json", warnings[0])

    def test_page_working_set_is_bound_to_head_artifacts_and_is_cleaned_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.make_workspace(root)
            project_id = "project-a"
            source = root / "head-source.png"
            translated = root / "head-translated.png"
            cache = root / "head-cache.json"
            source.write_bytes(b"head source")
            translated.write_bytes(b"head translated")
            cache.write_text("head cache", encoding="utf-8")
            workspace.commit_project_head(
                project_id,
                state_document={"schema_version": 2, "project_id": project_id},
                project_manifest={"project_id": project_id, "title": "Project A"},
                page_documents={
                    "001.png": {"page_id": "001.png", "metadata": {"revision": 4}}
                },
                artifact_files={
                    "source/001.png": source,
                    "translated/001.png": translated,
                    "cache/001.png/regions.json": cache,
                },
            )
            source.write_bytes(b"stale live source")
            translated.write_bytes(b"stale live translated")
            cache.write_text("stale live cache", encoding="utf-8")

            base = workspace.read_command_base(project_id, "001.png")
            with workspace.materialize_page_working_set(base) as working_set:
                working_root = working_set.root
                self.assertEqual(base.page_revision, 4)
                self.assertEqual(
                    (working_set.source_dir / "001.png").read_bytes(),
                    b"head source",
                )
                self.assertEqual(
                    (working_set.translated_dir / "001.png").read_bytes(),
                    b"head translated",
                )
                self.assertEqual(
                    (working_set.cache_dir / "001.png" / "regions.json").read_text(
                        encoding="utf-8"
                    ),
                    "head cache",
                )

            self.assertFalse(working_root.exists())

    def test_legacy_page_working_set_materializes_from_legacy_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.make_workspace(root)
            project_id = "legacy-project"
            source_dir = root / "legacy-source"
            translated_dir = root / "legacy-translated"
            cache_dir = root / "legacy-cache"
            source_dir.mkdir()
            translated_dir.mkdir()
            (cache_dir / "001.png").mkdir(parents=True)
            (source_dir / "001.png").write_bytes(b"legacy source")
            (translated_dir / "001.png").write_bytes(b"legacy translated")
            (cache_dir / "001.png" / "regions.json").write_text(
                "legacy cache",
                encoding="utf-8",
            )
            legacy_state = {
                "project_id": project_id,
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "rerender_cache_dir": str(cache_dir),
            }
            workspace.write_json_file(
                workspace.project_session_state_path(project_id),
                legacy_state,
            )
            workspace.write_json_file(
                workspace.project_manifest_path(project_id),
                {"project_id": project_id, "title": "Legacy"},
            )
            workspace.write_json_file(
                workspace.project_page_document_path(project_id, "001.png"),
                {"page_id": "001.png", "metadata": {"revision": 1}},
            )

            base = workspace.read_command_base(project_id, "001.png")
            with workspace.materialize_page_working_set(
                base,
                legacy_project=legacy_state,
            ) as working_set:
                self.assertIsNone(base.head)
                self.assertEqual(base.head_generation, 0)
                self.assertEqual(
                    (working_set.source_dir / "001.png").read_bytes(),
                    b"legacy source",
                )
                self.assertEqual(
                    (working_set.translated_dir / "001.png").read_bytes(),
                    b"legacy translated",
                )
                self.assertEqual(
                    (working_set.cache_dir / "001.png" / "regions.json").read_text(
                        encoding="utf-8"
                    ),
                    "legacy cache",
                )

    def test_json_helpers_default_bad_json_and_count_page_regions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            document_path = workspace.project_page_document_path("project-a", "001.png")

            self.assertEqual(workspace.read_json_file(document_path, {"missing": True}), {"missing": True})

            document_path.parent.mkdir(parents=True)
            document_path.write_text("{not json", encoding="utf-8")
            self.assertEqual(workspace.read_json_file(document_path, {"bad": True}), {"bad": True})
            self.assertEqual(workspace.page_document_region_count("project-a", "001.png"), 0)

            workspace.write_json_file(
                document_path,
                {"regions": [{"id": "a"}, "skip", {"id": "b"}]},
            )

            self.assertEqual(workspace.page_document_region_count("project-a", "001.png"), 2)
            self.assertEqual(
                workspace.project_region_count(
                    "project-a",
                    {"source_images": [{"stored_name": "001.png"}, {"stored_name": "missing.png"}]},
                ),
                2,
            )

    def test_snapshot_manifests_and_project_index_are_sorted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            snapshots_dir = workspace.project_snapshots_dir("project-a")
            snapshots_dir.mkdir(parents=True)
            workspace.write_json_file(
                snapshots_dir / "older.json",
                {"snapshot_id": "older", "created_at": "2026-01-01T00:00:00+00:00"},
            )
            workspace.write_json_file(
                snapshots_dir / "newer.json",
                {"snapshot_id": "newer", "created_at": "2026-01-02T00:00:00+00:00"},
            )

            snapshots = workspace.read_snapshot_manifests("project-a")

            self.assertEqual([item["snapshot_id"] for item in snapshots], ["newer", "older"])
            self.assertTrue(all("_path" in item for item in snapshots))

            workspace.write_project_index([
                {"project_id": "older-project", "updated_at": "2026-01-01T00:00:00+00:00"},
                {"project_id": "newer-project", "updated_at": "2026-01-03T00:00:00+00:00"},
            ])
            self.assertEqual(
                [item["project_id"] for item in workspace.read_json_file(workspace.project_index_path, [])],
                ["newer-project", "older-project"],
            )

            workspace.refresh_project_index_entry(
                {"project_id": "older-project", "updated_at": "2026-01-04T00:00:00+00:00"}
            )
            self.assertEqual(
                [item["project_id"] for item in workspace.read_json_file(workspace.project_index_path, [])],
                ["older-project", "newer-project"],
            )

    def test_snapshot_manifest_catalog_rejects_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            snapshots_dir = workspace.project_snapshots_dir("project-a")
            snapshots_dir.mkdir(parents=True)
            (snapshots_dir / "broken.json").write_text(
                "{not-json",
                encoding="utf-8",
            )

            with self.assertRaises(CorruptProjectArtifactError):
                workspace.read_snapshot_manifests("project-a")

    def test_snapshot_manifest_catalog_rejects_invalid_identity(self) -> None:
        cases = (
            ("non-object", ["not", "an", "object"]),
            ("missing snapshot id", {"created_at": "2026-01-01T00:00:00+00:00"}),
            (
                "snapshot id with surrounding whitespace",
                {
                    "snapshot_id": " manifest ",
                    "created_at": "2026-01-01T00:00:00+00:00",
                },
            ),
            (
                "blank snapshot id",
                {
                    "snapshot_id": "   ",
                    "created_at": "2026-01-01T00:00:00+00:00",
                },
            ),
            ("unsafe snapshot id", {"snapshot_id": "../bad", "created_at": "2026-01-01T00:00:00+00:00"}),
            ("missing created at", {"snapshot_id": "manifest"}),
            ("blank created at", {"snapshot_id": "manifest", "created_at": "  "}),
            (
                "filename mismatch",
                {
                    "snapshot_id": "different",
                    "created_at": "2026-01-01T00:00:00+00:00",
                },
            ),
        )
        for label, payload in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                workspace = self.make_workspace(Path(tmp))
                snapshots_dir = workspace.project_snapshots_dir("project-a")
                snapshots_dir.mkdir(parents=True)
                workspace.write_json_file(snapshots_dir / "manifest.json", payload)

                with self.assertRaises(CorruptProjectArtifactError):
                    workspace.read_snapshot_manifests("project-a")

    def test_snapshot_manifest_duplicate_identity_is_rejected_before_filename_mismatch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            snapshots_dir = workspace.project_snapshots_dir("project-a")
            snapshots_dir.mkdir(parents=True)
            for filename in ("same.json", "different.json"):
                workspace.write_json_file(
                    snapshots_dir / filename,
                    {
                        "snapshot_id": "same",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                )

            with self.assertRaisesRegex(CorruptProjectArtifactError, "重复"):
                workspace.read_snapshot_manifests("project-a")

    def test_snapshot_manifest_catalog_accepts_empty_and_legacy_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))

            self.assertEqual(workspace.read_snapshot_manifests("project-a"), [])

            snapshots_dir = workspace.project_snapshots_dir("project-a")
            snapshots_dir.mkdir(parents=True)
            snapshot_id = "20260718T120000+0000_legacy"
            workspace.write_json_file(
                snapshots_dir / f"{snapshot_id}.json",
                {
                    "snapshot_id": snapshot_id,
                    "created_at": "2026-07-18T12:00:00+00:00",
                    "kind": "legacy",
                },
            )
            internal_space_id = "legacy manifest"
            workspace.write_json_file(
                snapshots_dir / f"{internal_space_id}.json",
                {
                    "snapshot_id": internal_space_id,
                    "created_at": "2026-07-17T12:00:00+00:00",
                    "kind": "legacy",
                },
            )

            manifests = workspace.read_snapshot_manifests("project-a")

            self.assertEqual(
                [item["snapshot_id"] for item in manifests],
                [snapshot_id, internal_space_id],
            )
            self.assertTrue(
                all("artifact_bundle" not in item for item in manifests)
            )

    def test_snapshot_pin_limit_rejects_an_eleventh_pin_without_changing_bytes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            snapshots_dir = workspace.project_snapshots_dir("project-a")
            snapshots_dir.mkdir(parents=True)
            for index in range(10):
                snapshot_id = f"pinned-{index}"
                workspace.write_json_file(
                    snapshots_dir / f"{snapshot_id}.json",
                    {
                        "snapshot_id": snapshot_id,
                        "created_at": f"2026-07-01T00:{index:02d}:00+00:00",
                        "pinned": True,
                    },
                )
            target_path = snapshots_dir / "target.json"
            workspace.write_json_file(
                target_path,
                {
                    "snapshot_id": "target",
                    "created_at": "2026-07-02T00:00:00+00:00",
                    "pinned": False,
                },
            )
            target_bytes = target_path.read_bytes()

            with self.assertRaisesRegex(ValueError, "最多保留 10 个"):
                workspace.set_snapshot_pinned("project-a", "target", True)

            self.assertEqual(target_path.read_bytes(), target_bytes)

    def test_snapshot_pin_rejects_a_missing_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))

            with self.assertRaisesRegex(FileNotFoundError, "目标快照不存在"):
                workspace.set_snapshot_pinned(
                    "project-a",
                    "missing-snapshot",
                    True,
                )

    def test_snapshot_pin_matches_snapshot_identity_without_normalizing_it(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            snapshots_dir = workspace.project_snapshots_dir("project-a")
            snapshots_dir.mkdir(parents=True)
            target_path = snapshots_dir / "target.json"
            workspace.write_json_file(
                target_path,
                {
                    "snapshot_id": "target",
                    "created_at": "2026-07-02T00:00:00+00:00",
                    "pinned": False,
                },
            )
            target_bytes = target_path.read_bytes()

            with self.assertRaises(FileNotFoundError):
                workspace.set_snapshot_pinned("project-a", " target ", True)

            self.assertEqual(target_path.read_bytes(), target_bytes)

    def test_snapshot_pin_fails_closed_on_a_corrupt_catalog_without_changing_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            snapshots_dir = workspace.project_snapshots_dir("project-a")
            snapshots_dir.mkdir(parents=True)
            target_path = snapshots_dir / "target.json"
            broken_path = snapshots_dir / "broken.json"
            workspace.write_json_file(
                target_path,
                {
                    "snapshot_id": "target",
                    "created_at": "2026-07-02T00:00:00+00:00",
                    "pinned": False,
                },
            )
            broken_path.write_bytes(b"{corrupt snapshot evidence")
            original_bytes = {
                target_path: target_path.read_bytes(),
                broken_path: broken_path.read_bytes(),
            }

            with self.assertRaises(CorruptProjectArtifactError):
                workspace.set_snapshot_pinned("project-a", "target", True)

            self.assertEqual(
                {path: path.read_bytes() for path in original_bytes},
                original_bytes,
            )

    def test_snapshot_pin_returns_the_authoritative_catalog_after_retention(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            snapshots_dir = workspace.project_snapshots_dir("project-a")
            snapshots_dir.mkdir(parents=True)
            for index in range(31):
                snapshot_id = f"snapshot-{index:02d}"
                workspace.write_json_file(
                    snapshots_dir / f"{snapshot_id}.json",
                    {
                        "snapshot_id": snapshot_id,
                        "created_at": f"2026-07-01T00:{index:02d}:00+00:00",
                        "pinned": False,
                    },
                )

            returned = workspace.set_snapshot_pinned(
                "project-a",
                "snapshot-30",
                True,
            )
            authoritative = workspace.read_snapshot_manifests("project-a")

            self.assertEqual(returned, authoritative)
            self.assertEqual(len(returned), 21)
            self.assertNotIn(
                "snapshot-00",
                {item["snapshot_id"] for item in returned},
            )
            self.assertTrue(
                next(
                    item
                    for item in returned
                    if item["snapshot_id"] == "snapshot-30"
                )["pinned"]
            )

    def test_snapshot_pin_propagates_manifest_publication_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            snapshots_dir = workspace.project_snapshots_dir("project-a")
            snapshots_dir.mkdir(parents=True)
            target_path = snapshots_dir / "target.json"
            workspace.write_json_file(
                target_path,
                {
                    "snapshot_id": "target",
                    "created_at": "2026-07-02T00:00:00+00:00",
                    "pinned": False,
                },
            )
            target_bytes = target_path.read_bytes()

            with mock.patch(
                "engine.project_workspace.os.replace",
                side_effect=OSError("manifest publication unavailable"),
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "manifest publication unavailable",
                ):
                    workspace.set_snapshot_pinned("project-a", "target", True)

            self.assertEqual(target_path.read_bytes(), target_bytes)

    def test_snapshot_pin_propagates_retention_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            snapshots_dir = workspace.project_snapshots_dir("project-a")
            snapshots_dir.mkdir(parents=True)
            for index in range(21):
                snapshot_id = f"automatic-{index:02d}"
                workspace.write_json_file(
                    snapshots_dir / f"{snapshot_id}.json",
                    {
                        "snapshot_id": snapshot_id,
                        "created_at": f"2026-07-01T00:{index:02d}:00+00:00",
                        "pinned": False,
                    },
                )
            workspace.write_json_file(
                snapshots_dir / "target.json",
                {
                    "snapshot_id": "target",
                    "created_at": "2026-07-02T00:00:00+00:00",
                    "pinned": True,
                },
            )

            with mock.patch.object(
                Path,
                "unlink",
                side_effect=OSError("snapshot retention unavailable"),
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "snapshot retention unavailable",
                ):
                    workspace.set_snapshot_pinned("project-a", "target", False)

    def test_snapshot_pins_for_different_projects_remain_independent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            target_paths: dict[str, Path] = {}
            for project_id in ("project-a", "project-b"):
                snapshots_dir = workspace.project_snapshots_dir(project_id)
                snapshots_dir.mkdir(parents=True)
                target_path = snapshots_dir / "target.json"
                workspace.write_json_file(
                    target_path,
                    {
                        "snapshot_id": "target",
                        "created_at": "2026-07-02T00:00:00+00:00",
                        "pinned": False,
                    },
                )
                target_paths[project_id] = target_path

            project_a_write_reached = threading.Event()
            allow_project_a_write = threading.Event()
            project_b_completed = threading.Event()
            thread_errors: list[BaseException] = []
            results: dict[str, list[dict[str, object]]] = {}
            original_write_json = workspace.write_json_file

            def pause_project_a_write(path: Path, payload: object) -> None:
                if (
                    threading.current_thread().name == "project-a-pin"
                    and Path(path) == target_paths["project-a"]
                ):
                    project_a_write_reached.set()
                    if not allow_project_a_write.wait(timeout=5):
                        raise RuntimeError("project-a pin was not released")
                original_write_json(path, payload)

            def pin_project(project_id: str) -> None:
                try:
                    results[project_id] = workspace.set_snapshot_pinned(
                        project_id,
                        "target",
                        True,
                    )
                    if project_id == "project-b":
                        project_b_completed.set()
                except BaseException as exc:
                    thread_errors.append(exc)

            self.addCleanup(allow_project_a_write.set)
            with mock.patch.object(
                workspace,
                "write_json_file",
                side_effect=pause_project_a_write,
            ):
                project_a_thread = threading.Thread(
                    target=pin_project,
                    args=("project-a",),
                    name="project-a-pin",
                    daemon=True,
                )
                project_a_thread.start()
                self.assertTrue(project_a_write_reached.wait(timeout=5))

                project_b_thread = threading.Thread(
                    target=pin_project,
                    args=("project-b",),
                    name="project-b-pin",
                    daemon=True,
                )
                project_b_thread.start()
                self.assertTrue(project_b_completed.wait(timeout=5))

                allow_project_a_write.set()
                project_a_thread.join(timeout=5)
                project_b_thread.join(timeout=5)
                self.assertFalse(project_a_thread.is_alive())
                self.assertFalse(project_b_thread.is_alive())

            self.assertEqual(thread_errors, [])
            self.assertEqual(set(results), {"project-a", "project-b"})
            self.assertTrue(results["project-a"][0]["pinned"])
            self.assertTrue(results["project-b"][0]["pinned"])

    def test_snapshot_artifacts_are_content_addressed_restorable_and_collectable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.make_workspace(root)
            source = root / "artifact.txt"
            source.write_text("same historical bytes", encoding="utf-8")

            bundle = workspace.capture_snapshot_artifacts(
                "project-a",
                {
                    "source/page.txt": source,
                    "cache/page/marker.txt": source,
                },
            )

            blob_ids = {metadata["blob"] for metadata in bundle["files"].values()}
            self.assertEqual(len(blob_ids), 1)
            blob_files = list(workspace.project_snapshot_blobs_dir("project-a").glob("[0-9a-f][0-9a-f]/*"))
            self.assertEqual(len(blob_files), 1)

            with mock.patch.object(workspace, "_sha256_file", wraps=workspace._sha256_file) as hash_file:
                reused_bundle = workspace.capture_snapshot_artifacts(
                    "project-a",
                    {
                        "source/page.txt": source,
                        "cache/page/marker.txt": source,
                    },
                    previous_bundle=bundle,
                )
            self.assertEqual(hash_file.call_count, 0)
            self.assertEqual(
                {metadata["blob"] for metadata in reused_bundle["files"].values()},
                blob_ids,
            )

            restored_source = root / "restored-source"
            restored_cache = root / "restored-cache"
            restored_roots = workspace.restore_snapshot_artifacts(
                "project-a",
                bundle,
                {"source": restored_source, "cache": restored_cache},
            )
            self.assertEqual(restored_roots, {"source", "cache"})
            self.assertEqual((restored_source / "page.txt").read_text(encoding="utf-8"), "same historical bytes")
            self.assertEqual((restored_cache / "page" / "marker.txt").read_text(encoding="utf-8"), "same historical bytes")

            workspace.garbage_collect_snapshot_blobs("project-a")
            self.assertFalse(blob_files[0].exists())

    def test_artifact_gc_keeps_only_head_and_snapshot_revision_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            project_id = "project-a"
            state_document = {"schema_version": 2, "project_id": project_id}
            project_manifest = {"project_id": project_id, "title": "Project A"}
            first_head = workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest=project_manifest,
                page_documents={"001.png": {"page_id": "001.png", "metadata": {"revision": 1}}},
            )
            first_page_blob = first_head["files"]["pages/001.png/page_document.json"]["blob"]
            second_head = workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest=project_manifest,
                page_documents={"001.png": {"page_id": "001.png", "metadata": {"revision": 2}}},
                expected_generation=1,
            )
            second_page_blob = second_head["files"]["pages/001.png/page_document.json"]["blob"]
            first_revision_path = workspace.project_revisions_dir(project_id) / f"{first_head['revision_id']}.json"
            second_revision_path = workspace.project_revisions_dir(project_id) / f"{second_head['revision_id']}.json"
            first_blob_path = workspace.project_artifact_store_dir(project_id) / first_page_blob[:2] / first_page_blob
            second_blob_path = workspace.project_artifact_store_dir(project_id) / second_page_blob[:2] / second_page_blob
            snapshot = workspace.create_project_head_snapshot(
                project_id,
                first_head,
                {
                    "kind": "manual",
                    "summary": "Keep the first Head",
                    "created_at": "2026-07-19T05:00:00+00:00",
                },
            )

            workspace.garbage_collect_snapshot_blobs(project_id)

            self.assertTrue(first_revision_path.exists())
            self.assertTrue(second_revision_path.exists())
            self.assertTrue(first_blob_path.exists())
            self.assertTrue(second_blob_path.exists())

            (
                workspace.project_snapshots_dir(project_id)
                / f"{snapshot['snapshot_id']}.json"
            ).unlink()
            workspace.garbage_collect_snapshot_blobs(project_id)

            self.assertFalse(first_revision_path.exists())
            self.assertTrue(second_revision_path.exists())
            self.assertFalse(first_blob_path.exists())
            self.assertTrue(second_blob_path.exists())

    def test_snapshot_publication_rejects_unsafe_raw_revision_identifiers(self) -> None:
        invalid_revision_ids = (
            "",
            " padded-revision ",
            "../outside",
            "nested/revision",
            "nested\\revision",
            "/absolute-revision",
        )
        for revision_id in invalid_revision_ids:
            with self.subTest(revision_id=repr(revision_id)), tempfile.TemporaryDirectory() as tmp:
                workspace = self.make_workspace(Path(tmp))
                project_id = "project-a"
                head = workspace.commit_project_head(
                    project_id,
                    state_document={"schema_version": 2, "project_id": project_id},
                    project_manifest={"project_id": project_id, "title": "Project A"},
                    page_documents={
                        "001.png": {
                            "page_id": "001.png",
                            "metadata": {"revision": 1},
                        }
                    },
                )
                workspace.create_project_head_snapshot(
                    project_id,
                    head,
                    {
                        "kind": "manual",
                        "created_at": "2026-07-19T05:00:00+00:00",
                    },
                )
                catalog_before = self.snapshot_catalog_bytes(workspace, project_id)
                invalid_head = {**head, "revision_id": revision_id}

                with self.assertRaises(CorruptProjectArtifactError):
                    workspace.create_project_head_snapshot(
                        project_id,
                        invalid_head,
                        {
                            "kind": "manual",
                            "created_at": "2026-07-19T06:00:00+00:00",
                        },
                    )

                self.assertEqual(
                    self.snapshot_catalog_bytes(workspace, project_id),
                    catalog_before,
                )

    def test_snapshot_publication_rejects_missing_or_malformed_revision_document(self) -> None:
        cases = ("missing", "malformed-json", "non-object")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                workspace = self.make_workspace(Path(tmp))
                project_id = "project-a"
                head = workspace.commit_project_head(
                    project_id,
                    state_document={"schema_version": 2, "project_id": project_id},
                    project_manifest={"project_id": project_id, "title": "Project A"},
                    page_documents={
                        "001.png": {
                            "page_id": "001.png",
                            "metadata": {"revision": 1},
                        }
                    },
                )
                workspace.create_project_head_snapshot(
                    project_id,
                    head,
                    {
                        "kind": "manual",
                        "created_at": "2026-07-19T05:00:00+00:00",
                    },
                )
                catalog_before = self.snapshot_catalog_bytes(workspace, project_id)
                revision_path = (
                    workspace.project_revisions_dir(project_id)
                    / f"{head['revision_id']}.json"
                )
                if case == "missing":
                    revision_path.unlink()
                elif case == "malformed-json":
                    revision_path.write_text("{not-json", encoding="utf-8")
                else:
                    workspace.write_json_file(revision_path, ["not", "an", "object"])

                with self.assertRaises(CorruptProjectArtifactError):
                    workspace.create_project_head_snapshot(
                        project_id,
                        head,
                        {
                            "kind": "manual",
                            "created_at": "2026-07-19T06:00:00+00:00",
                        },
                    )

                self.assertEqual(
                    self.snapshot_catalog_bytes(workspace, project_id),
                    catalog_before,
                )

    def test_snapshot_publication_requires_head_to_match_stored_revision(self) -> None:
        cases = (
            ("schema", {"schema_version": 2}),
            ("project", {"project_id": "other-project"}),
            ("generation", {"generation": 2}),
            ("boolean generation", {"generation": True}),
            ("revision", {"revision_id": "g00000001-other"}),
            ("files", {"files": {}}),
        )
        for case, replacement in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                workspace = self.make_workspace(Path(tmp))
                project_id = "project-a"
                head = workspace.commit_project_head(
                    project_id,
                    state_document={"schema_version": 2, "project_id": project_id},
                    project_manifest={"project_id": project_id, "title": "Project A"},
                    page_documents={
                        "001.png": {
                            "page_id": "001.png",
                            "metadata": {"revision": 1},
                        }
                    },
                )
                workspace.create_project_head_snapshot(
                    project_id,
                    head,
                    {
                        "kind": "manual",
                        "created_at": "2026-07-19T05:00:00+00:00",
                    },
                )
                catalog_before = self.snapshot_catalog_bytes(workspace, project_id)
                revision_path = (
                    workspace.project_revisions_dir(project_id)
                    / f"{head['revision_id']}.json"
                )
                stored_revision = copy.deepcopy(head)
                stored_revision.update(copy.deepcopy(replacement))
                workspace.write_json_file(revision_path, stored_revision)

                with self.assertRaises(CorruptProjectArtifactError):
                    workspace.create_project_head_snapshot(
                        project_id,
                        head,
                        {
                            "kind": "manual",
                            "created_at": "2026-07-19T06:00:00+00:00",
                        },
                    )

                self.assertEqual(
                    self.snapshot_catalog_bytes(workspace, project_id),
                    catalog_before,
                )

    def test_snapshot_publication_rejects_stored_file_metadata_type_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            project_id = "project-a"
            head = workspace.commit_project_head(
                project_id,
                state_document={"schema_version": 2, "project_id": project_id},
                project_manifest={"project_id": project_id, "title": "Project A"},
                page_documents={
                    "001.png": {
                        "page_id": "001.png",
                        "metadata": {"revision": 1},
                    }
                },
            )
            workspace.create_project_head_snapshot(
                project_id,
                head,
                {
                    "kind": "manual",
                    "created_at": "2026-07-19T05:00:00+00:00",
                },
            )
            catalog_before = self.snapshot_catalog_bytes(workspace, project_id)
            revision_path = (
                workspace.project_revisions_dir(project_id)
                / f"{head['revision_id']}.json"
            )
            stored_revision = copy.deepcopy(head)
            stored_metadata = next(iter(stored_revision["files"].values()))
            stored_metadata["size"] = float(stored_metadata["size"])
            workspace.write_json_file(revision_path, stored_revision)

            with self.assertRaises(CorruptProjectArtifactError):
                workspace.create_project_head_snapshot(
                    project_id,
                    head,
                    {
                        "kind": "manual",
                        "created_at": "2026-07-19T06:00:00+00:00",
                    },
                )

            self.assertEqual(
                self.snapshot_catalog_bytes(workspace, project_id),
                catalog_before,
            )

    def test_snapshot_publication_rejects_unsafe_or_noncanonical_logical_paths(self) -> None:
        invalid_paths = (
            " source/001.png ",
            "source\\001.png",
            "source//001.png",
            "source/../001.png",
            "../outside.png",
            "/absolute.png",
        )
        for logical_path in invalid_paths:
            with self.subTest(logical_path=repr(logical_path)), tempfile.TemporaryDirectory() as tmp:
                workspace = self.make_workspace(Path(tmp))
                project_id = "project-a"
                head = workspace.commit_project_head(
                    project_id,
                    state_document={"schema_version": 2, "project_id": project_id},
                    project_manifest={"project_id": project_id, "title": "Project A"},
                    page_documents={
                        "001.png": {
                            "page_id": "001.png",
                            "metadata": {"revision": 1},
                        }
                    },
                )
                workspace.create_project_head_snapshot(
                    project_id,
                    head,
                    {
                        "kind": "manual",
                        "created_at": "2026-07-19T05:00:00+00:00",
                    },
                )
                catalog_before = self.snapshot_catalog_bytes(workspace, project_id)
                metadata = next(iter(head["files"].values()))
                invalid_head = {
                    **head,
                    "files": {**head["files"], logical_path: metadata},
                }
                revision_path = (
                    workspace.project_revisions_dir(project_id)
                    / f"{head['revision_id']}.json"
                )
                workspace.write_json_file(revision_path, invalid_head)

                with self.assertRaises(CorruptProjectArtifactError):
                    workspace.create_project_head_snapshot(
                        project_id,
                        invalid_head,
                        {
                            "kind": "manual",
                            "created_at": "2026-07-19T06:00:00+00:00",
                        },
                    )

                self.assertEqual(
                    self.snapshot_catalog_bytes(workspace, project_id),
                    catalog_before,
                )

    def test_snapshot_publication_rejects_malformed_blob_records(self) -> None:
        cases = (
            "metadata-not-object",
            "padded-digest",
            "uppercase-digest",
            "invalid-digest",
            "missing-size",
            "boolean-size",
            "negative-size",
            "string-size",
            "mismatched-size",
        )
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                workspace = self.make_workspace(Path(tmp))
                project_id = "project-a"
                head = workspace.commit_project_head(
                    project_id,
                    state_document={"schema_version": 2, "project_id": project_id},
                    project_manifest={"project_id": project_id, "title": "Project A"},
                    page_documents={
                        "001.png": {
                            "page_id": "001.png",
                            "metadata": {"revision": 1},
                        }
                    },
                )
                workspace.create_project_head_snapshot(
                    project_id,
                    head,
                    {
                        "kind": "manual",
                        "created_at": "2026-07-19T05:00:00+00:00",
                    },
                )
                catalog_before = self.snapshot_catalog_bytes(workspace, project_id)
                logical_path, original_metadata = next(iter(head["files"].items()))
                if case == "metadata-not-object":
                    invalid_metadata: object = ["not", "an", "object"]
                else:
                    invalid_metadata = copy.deepcopy(original_metadata)
                    if case == "padded-digest":
                        invalid_metadata["blob"] = f" {original_metadata['blob']} "
                    elif case == "uppercase-digest":
                        invalid_metadata["blob"] = original_metadata["blob"].upper()
                    elif case == "invalid-digest":
                        invalid_metadata["blob"] = "not-a-digest"
                    elif case == "missing-size":
                        invalid_metadata.pop("size")
                    elif case == "boolean-size":
                        invalid_metadata["size"] = True
                    elif case == "negative-size":
                        invalid_metadata["size"] = -1
                    elif case == "string-size":
                        invalid_metadata["size"] = str(original_metadata["size"])
                    else:
                        invalid_metadata["size"] = original_metadata["size"] + 1
                invalid_head = {
                    **head,
                    "files": {**head["files"], logical_path: invalid_metadata},
                }
                revision_path = (
                    workspace.project_revisions_dir(project_id)
                    / f"{head['revision_id']}.json"
                )
                workspace.write_json_file(revision_path, invalid_head)

                with self.assertRaises(CorruptProjectArtifactError):
                    workspace.create_project_head_snapshot(
                        project_id,
                        invalid_head,
                        {
                            "kind": "manual",
                            "created_at": "2026-07-19T06:00:00+00:00",
                        },
                    )

                self.assertEqual(
                    self.snapshot_catalog_bytes(workspace, project_id),
                    catalog_before,
                )

    def test_snapshot_publication_rejects_missing_or_tampered_blobs(self) -> None:
        for case in ("missing", "tampered"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                workspace = self.make_workspace(Path(tmp))
                project_id = "project-a"
                head = workspace.commit_project_head(
                    project_id,
                    state_document={"schema_version": 2, "project_id": project_id},
                    project_manifest={"project_id": project_id, "title": "Project A"},
                    page_documents={
                        "001.png": {
                            "page_id": "001.png",
                            "metadata": {"revision": 1},
                        }
                    },
                )
                workspace.create_project_head_snapshot(
                    project_id,
                    head,
                    {
                        "kind": "manual",
                        "created_at": "2026-07-19T05:00:00+00:00",
                    },
                )
                catalog_before = self.snapshot_catalog_bytes(workspace, project_id)
                metadata = next(iter(head["files"].values()))
                blob_id = metadata["blob"]
                blob_path = (
                    workspace.project_artifact_store_dir(project_id)
                    / blob_id[:2]
                    / blob_id
                )
                if case == "missing":
                    blob_path.unlink()
                else:
                    blob_path.write_bytes(b"x" * metadata["size"])

                with self.assertRaises(CorruptProjectArtifactError):
                    workspace.create_project_head_snapshot(
                        project_id,
                        head,
                        {
                            "kind": "manual",
                            "created_at": "2026-07-19T06:00:00+00:00",
                        },
                    )

                self.assertEqual(
                    self.snapshot_catalog_bytes(workspace, project_id),
                    catalog_before,
                )

    def test_snapshot_publication_rejects_created_at_that_makes_unsafe_snapshot_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            invalid_created_at_values = (
                "",
                "   ",
                " padded ",
                "../outside",
                "nested/snapshot",
                "nested\\snapshot",
                str(root / "absolute-snapshot"),
            )
            for created_at in invalid_created_at_values:
                with self.subTest(created_at=repr(created_at)):
                    case_root = root / f"case-{invalid_created_at_values.index(created_at)}"
                    workspace = self.make_workspace(case_root)
                    project_id = "project-a"
                    head = workspace.commit_project_head(
                        project_id,
                        state_document={"schema_version": 2, "project_id": project_id},
                        project_manifest={"project_id": project_id, "title": "Project A"},
                        page_documents={
                            "001.png": {
                                "page_id": "001.png",
                                "metadata": {"revision": 1},
                            }
                        },
                    )
                    workspace.create_project_head_snapshot(
                        project_id,
                        head,
                        {
                            "kind": "manual",
                            "created_at": "2026-07-19T05:00:00+00:00",
                        },
                    )
                    tree_before = self.file_tree_bytes(root)

                    with self.assertRaises(CorruptProjectArtifactError):
                        workspace.create_project_head_snapshot(
                            project_id,
                            head,
                            {"kind": "manual", "created_at": created_at},
                        )

                    self.assertEqual(self.file_tree_bytes(root), tree_before)

    def test_snapshot_publication_can_restore_a_retained_older_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.make_workspace(root)
            project_id = "project-a"
            state_document = {"schema_version": 2, "project_id": project_id}
            project_manifest = {"project_id": project_id, "title": "Project A"}
            first_head = workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest=project_manifest,
                page_documents={
                    "001.png": {
                        "page_id": "001.png",
                        "metadata": {"revision": 1},
                    }
                },
            )
            workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest=project_manifest,
                page_documents={
                    "001.png": {
                        "page_id": "001.png",
                        "metadata": {"revision": 2},
                    }
                },
                expected_generation=first_head["generation"],
                expected_revision_id=first_head["revision_id"],
            )

            snapshot = workspace.create_project_head_snapshot(
                project_id,
                first_head,
                {"kind": "manual", "summary": "Retained older Head"},
            )
            restored_state = root / "restored-state"
            restored_project = root / "restored-project"
            restored_pages = root / "restored-pages"
            restored_roots = workspace.restore_snapshot_artifacts(
                project_id,
                snapshot["artifact_bundle"],
                {
                    "state": restored_state,
                    "project": restored_project,
                    "pages": restored_pages,
                },
            )

            self.assertEqual(restored_roots, {"state", "project", "pages"})
            restored_page = json.loads(
                (restored_pages / "001.png" / "page_document.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(restored_page["metadata"]["revision"], 1)
            self.assertNotEqual(
                first_head["revision_id"],
                workspace.read_project_head(project_id)["revision_id"],
            )

    def test_artifact_gc_uses_snapshot_catalog_published_after_a_stale_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.make_workspace(root)
            project_id = "project-a"
            state_document = {"schema_version": 2, "project_id": project_id}
            project_manifest = {"project_id": project_id, "title": "Project A"}
            first_head = workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest=project_manifest,
                page_documents={
                    "001.png": {
                        "page_id": "001.png",
                        "metadata": {"revision": 1},
                    }
                },
            )
            stale_manifests = workspace.read_snapshot_manifests(project_id)
            self.assertEqual(stale_manifests, [])
            workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest=project_manifest,
                page_documents={
                    "001.png": {
                        "page_id": "001.png",
                        "metadata": {"revision": 2},
                    }
                },
                expected_generation=first_head["generation"],
                expected_revision_id=first_head["revision_id"],
            )
            first_blob_id = first_head["files"][
                "pages/001.png/page_document.json"
            ]["blob"]
            first_blob_path = (
                workspace.project_artifact_store_dir(project_id)
                / first_blob_id[:2]
                / first_blob_id
            )
            first_revision_path = (
                workspace.project_revisions_dir(project_id)
                / f"{first_head['revision_id']}.json"
            )
            snapshots_dir = workspace.project_snapshots_dir(project_id)
            publication_reached = threading.Event()
            allow_publication = threading.Event()
            gc_lock_attempted = threading.Event()
            thread_errors: list[BaseException] = []
            published_snapshots: list[dict[str, object]] = []
            original_write_json = workspace.write_json_file
            project_lock = workspace._head_commit_lock(project_id)

            def pause_before_snapshot_publication(
                path: Path,
                payload: dict[str, object],
            ) -> None:
                if (
                    threading.current_thread().name == "snapshot-writer"
                    and Path(path).parent == snapshots_dir
                ):
                    publication_reached.set()
                    if not allow_publication.wait(timeout=5):
                        raise RuntimeError("snapshot publication was not released")
                original_write_json(path, payload)

            def observe_project_lock(requested_project_id: str):
                if threading.current_thread().name == "snapshot-artifact-gc":
                    gc_lock_attempted.set()
                return project_lock

            def publish_snapshot() -> None:
                try:
                    published_snapshots.append(
                        workspace.create_project_head_snapshot(
                            project_id,
                            first_head,
                            {
                                "kind": "manual",
                                "summary": "Keep the first Head",
                                "created_at": "2026-07-19T06:00:00+00:00",
                            },
                        )
                    )
                except BaseException as exc:
                    thread_errors.append(exc)

            def collect_artifacts() -> None:
                try:
                    workspace.garbage_collect_snapshot_blobs(project_id)
                except BaseException as exc:
                    thread_errors.append(exc)

            self.addCleanup(allow_publication.set)
            with (
                mock.patch.object(
                    workspace,
                    "write_json_file",
                    side_effect=pause_before_snapshot_publication,
                ),
                mock.patch.object(
                    workspace,
                    "_head_commit_lock",
                    side_effect=observe_project_lock,
                ),
            ):
                writer = threading.Thread(
                    target=publish_snapshot,
                    name="snapshot-writer",
                    daemon=True,
                )
                writer.start()
                self.assertTrue(publication_reached.wait(timeout=5))

                collector = threading.Thread(
                    target=collect_artifacts,
                    name="snapshot-artifact-gc",
                    daemon=True,
                )
                collector.start()
                self.assertTrue(gc_lock_attempted.wait(timeout=5))

                allow_publication.set()
                writer.join(timeout=5)
                collector.join(timeout=5)
                self.assertFalse(writer.is_alive())
                self.assertFalse(collector.is_alive())

            self.assertEqual(thread_errors, [])
            self.assertEqual(len(published_snapshots), 1)
            snapshot = published_snapshots[0]
            self.assertEqual(len(workspace.read_snapshot_manifests(project_id)), 1)

            restored_pages = root / "restored-pages"
            workspace.restore_snapshot_artifacts(
                project_id,
                snapshot["artifact_bundle"],
                {"pages": restored_pages},
            )
            restored_page_document = json.loads(
                (restored_pages / "001.png" / "page_document.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(restored_page_document["metadata"]["revision"], 1)
            self.assertTrue(first_revision_path.is_file())
            self.assertTrue(first_blob_path.is_file())

    def test_pending_artifact_set_shares_the_store_and_is_a_gc_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.make_workspace(root)
            pending_output = root / "pending-page.png"
            pending_output.write_bytes(b"completed pending page")

            pending = workspace.write_pending_artifact_set(
                "project-a",
                action="rerender",
                resume_fingerprint="fingerprint-a",
                base_head=None,
                state_document={
                    "schema_version": 2,
                    "project_id": "project-a",
                    "source_images": [],
                    "workflow_stage": "translated",
                    "artifact_state": {"schema_version": 2, "pages": {}},
                },
                files={"translated/001.png": pending_output},
            )
            self.assertEqual(pending["schema_version"], 2)
            self.assertEqual(pending["page_checkpoints"], {})
            self.assertNotIn("completed_page_ids", pending)
            blob_id = pending["artifact_bundle"]["files"]["translated/001.png"]["blob"]
            blob_path = workspace.project_artifact_store_dir("project-a") / blob_id[:2] / blob_id
            restored_dir = root / "restored"

            workspace.garbage_collect_snapshot_blobs("project-a")
            workspace.restore_pending_artifact_set(
                "project-a",
                pending,
                {"translated": restored_dir},
            )

            self.assertTrue(blob_path.exists())
            self.assertEqual(
                (restored_dir / "001.png").read_bytes(),
                b"completed pending page",
            )
            self.assertEqual(
                workspace.read_pending_artifact_set("project-a")["resume_fingerprint"],
                "fingerprint-a",
            )

            workspace.clear_pending_artifact_set("project-a")
            workspace.garbage_collect_snapshot_blobs("project-a")

            self.assertIsNone(workspace.read_pending_artifact_set("project-a"))
            self.assertFalse(blob_path.exists())

    def test_clear_obsolete_pending_returns_false_when_pending_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))

            self.assertFalse(
                workspace.clear_obsolete_pending_artifact_set("project-a")
            )

    def test_clear_obsolete_pending_preserves_current_head_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            project_id = "project-a"
            state_document = {
                "schema_version": 2,
                "project_id": project_id,
                "source_images": [],
                "workflow_stage": "translated",
                "artifact_state": {"schema_version": 2, "pages": {}},
            }
            head = workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest={"project_id": project_id, "title": "Project A"},
                page_documents={},
            )
            workspace.write_pending_artifact_set(
                project_id,
                action="rerender",
                resume_fingerprint="current-head-pending",
                base_head=head,
                state_document=state_document,
                files={},
            )
            pending_path = workspace.project_pending_artifact_path(project_id)
            pending_bytes = pending_path.read_bytes()

            self.assertFalse(
                workspace.clear_obsolete_pending_artifact_set(project_id)
            )
            self.assertEqual(pending_path.read_bytes(), pending_bytes)

    def test_clear_obsolete_pending_deletes_pending_from_an_older_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            project_id = "project-a"
            state_document = {
                "schema_version": 2,
                "project_id": project_id,
                "source_images": [],
                "workflow_stage": "translated",
                "artifact_state": {"schema_version": 2, "pages": {}},
            }
            first_head = workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest={"project_id": project_id, "title": "Project A"},
                page_documents={},
            )
            workspace.write_pending_artifact_set(
                project_id,
                action="rerender",
                resume_fingerprint="older-head-pending",
                base_head=first_head,
                state_document=state_document,
                files={},
            )
            workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest={"project_id": project_id, "title": "Project A"},
                page_documents={},
                expected_generation=first_head["generation"],
                expected_revision_id=first_head["revision_id"],
            )

            self.assertTrue(
                workspace.clear_obsolete_pending_artifact_set(project_id)
            )
            self.assertIsNone(workspace.read_pending_artifact_set(project_id))

    def test_clear_obsolete_pending_compares_revision_identity_without_stripping(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            project_id = "project-a"
            state_document = {
                "schema_version": 2,
                "project_id": project_id,
                "source_images": [],
                "workflow_stage": "translated",
                "artifact_state": {"schema_version": 2, "pages": {}},
            }
            head = workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest={"project_id": project_id, "title": "Project A"},
                page_documents={},
            )
            padded_identity = {
                **head,
                "revision_id": f" {head['revision_id']} ",
            }
            workspace.write_pending_artifact_set(
                project_id,
                action="rerender",
                resume_fingerprint="padded-revision-identity",
                base_head=padded_identity,
                state_document=state_document,
                files={},
            )

            self.assertTrue(
                workspace.clear_obsolete_pending_artifact_set(project_id)
            )
            self.assertIsNone(workspace.read_pending_artifact_set(project_id))

    def test_clear_obsolete_pending_preserves_corrupt_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            pending_path = workspace.project_pending_artifact_path("project-a")
            pending_path.parent.mkdir(parents=True)
            corrupt_bytes = b'{"schema_version": 2, "project_id": "project-a"'
            pending_path.write_bytes(corrupt_bytes)

            with self.assertRaises(CorruptProjectArtifactError):
                workspace.clear_obsolete_pending_artifact_set("project-a")

            self.assertEqual(pending_path.read_bytes(), corrupt_bytes)

    def test_unconditional_pending_clear_waits_for_inflight_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            project_id = "project-a"
            state_document = {
                "schema_version": 2,
                "project_id": project_id,
                "source_images": [],
                "workflow_stage": "translated",
                "artifact_state": {"schema_version": 2, "pages": {}},
            }
            pending_path = workspace.project_pending_artifact_path(project_id)
            publication_reached = threading.Event()
            allow_publication = threading.Event()
            clear_lock_attempted = threading.Event()
            clear_finished = threading.Event()
            thread_errors: list[BaseException] = []
            original_write_json = workspace.write_json_file
            project_lock = workspace._head_commit_lock(project_id)
            self.addCleanup(allow_publication.set)

            def pause_before_pending_publication(
                path: Path,
                payload: dict[str, object],
            ) -> None:
                if (
                    threading.current_thread().name == "pending-writer"
                    and Path(path) == pending_path
                ):
                    publication_reached.set()
                    if not allow_publication.wait(timeout=5):
                        raise RuntimeError("pending publication was not released")
                original_write_json(path, payload)

            def observe_project_lock(requested_project_id: str):
                if threading.current_thread().name == "pending-clearer":
                    clear_lock_attempted.set()
                return project_lock

            def write_pending() -> None:
                try:
                    workspace.write_pending_artifact_set(
                        project_id,
                        action="rerender",
                        resume_fingerprint="inflight-pending",
                        base_head=None,
                        state_document=state_document,
                        files={},
                    )
                except BaseException as exc:
                    thread_errors.append(exc)

            def clear_pending() -> None:
                try:
                    workspace.clear_pending_artifact_set(project_id)
                except BaseException as exc:
                    thread_errors.append(exc)
                finally:
                    clear_finished.set()

            with (
                mock.patch.object(
                    workspace,
                    "write_json_file",
                    side_effect=pause_before_pending_publication,
                ),
                mock.patch.object(
                    workspace,
                    "_head_commit_lock",
                    side_effect=observe_project_lock,
                ),
            ):
                writer = threading.Thread(
                    target=write_pending,
                    name="pending-writer",
                    daemon=True,
                )
                writer.start()
                self.assertTrue(publication_reached.wait(timeout=5))

                clearer = threading.Thread(
                    target=clear_pending,
                    name="pending-clearer",
                    daemon=True,
                )
                clearer.start()
                attempted_lock = clear_lock_attempted.wait(timeout=1)
                finished_before_publication = clear_finished.is_set()

                allow_publication.set()
                writer.join(timeout=5)
                clearer.join(timeout=5)

            self.assertTrue(attempted_lock)
            self.assertFalse(finished_before_publication)
            self.assertFalse(writer.is_alive())
            self.assertFalse(clearer.is_alive())
            self.assertEqual(thread_errors, [])
            self.assertFalse(pending_path.exists())

    def test_page_commit_cleanup_preserves_concurrent_current_pending_and_its_blob(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.make_workspace(root)
            project_id = "project-a"
            page_id = "001.png"
            artifact_state = (
                ProjectArtifactState.create([page_id])
                .apply(page_id, PageArtifactEvent.RECOGNIZED)
                .apply(page_id, PageArtifactEvent.TRANSLATED)
            )
            state_document = {
                "schema_version": 2,
                "project_id": project_id,
                "source_images": [{"name": page_id, "stored_name": page_id}],
                "translated_output_map": {page_id: page_id},
                "workflow_stage": "translated",
                "artifact_state": artifact_state.model_dump(mode="json"),
            }
            project_manifest = {"project_id": project_id, "title": "Project A"}
            first_head = workspace.commit_project_head(
                project_id,
                state_document=state_document,
                project_manifest=project_manifest,
                page_documents={
                    page_id: {
                        "page_id": page_id,
                        "regions": [],
                        "metadata": {"revision": 1},
                    }
                },
            )
            workspace.write_pending_artifact_set(
                project_id,
                action="rerender",
                resume_fingerprint="obsolete-before-page-commit",
                base_head=first_head,
                state_document=state_document,
                files={},
            )
            base = workspace.read_command_base(project_id, page_id)
            concurrent_artifact = root / "concurrent-pending.bin"
            concurrent_artifact.write_bytes(b"concurrent current-head pending")
            pending_path = workspace.project_pending_artifact_path(project_id)
            stale_read_reached = threading.Event()
            allow_stale_read_to_return = threading.Event()
            writer_lock_attempted = threading.Event()
            writer_published = threading.Event()
            thread_errors: list[BaseException] = []
            committed_results: list[object] = []
            concurrent_pending: list[dict[str, object]] = []
            original_read_pending = workspace.read_pending_artifact_set
            original_clear_pending = workspace.clear_pending_artifact_set
            project_lock = workspace._head_commit_lock(project_id)
            paused_cleanup_read = False
            self.addCleanup(allow_stale_read_to_return.set)

            def pause_after_cleanup_reads_obsolete_pending(
                requested_project_id: str,
            ) -> dict[str, object] | None:
                nonlocal paused_cleanup_read
                pending = original_read_pending(requested_project_id)
                if (
                    threading.current_thread().name == "page-commit"
                    and not paused_cleanup_read
                ):
                    paused_cleanup_read = True
                    stale_read_reached.set()
                    if not allow_stale_read_to_return.wait(timeout=5):
                        raise RuntimeError("page cleanup read was not released")
                return pending

            def observe_project_lock(requested_project_id: str):
                if threading.current_thread().name == "current-pending-writer":
                    writer_lock_attempted.set()
                return project_lock

            def clear_only_after_concurrent_publication(
                requested_project_id: str,
            ) -> None:
                if threading.current_thread().name == "page-commit":
                    if not writer_published.wait(timeout=5):
                        raise RuntimeError("concurrent Pending was not published")
                original_clear_pending(requested_project_id)

            def commit_page(working_set) -> None:
                try:
                    committed_results.append(
                        workspace.commit_page_working_set(
                            working_set,
                            PreparedHeadUpdate(
                                state_document=state_document,
                                project_manifest=project_manifest,
                                page_documents={
                                    page_id: {
                                        "page_id": page_id,
                                        "regions": [],
                                        "metadata": {"revision": 2},
                                    }
                                },
                                artifact_files={},
                                replace_prefixes=(f"pages/{page_id}/",),
                                remove_logical_paths=set(),
                                runtime_session={},
                                execution_extras={},
                            ),
                        )
                    )
                except BaseException as exc:
                    thread_errors.append(exc)

            def publish_current_pending() -> None:
                try:
                    current_head = workspace.read_project_head(project_id)
                    concurrent_pending.append(
                        workspace.write_pending_artifact_set(
                            project_id,
                            action="rerender",
                            resume_fingerprint="concurrent-current-head",
                            base_head=current_head,
                            state_document=state_document,
                            files={
                                "translated/concurrent.bin": concurrent_artifact
                            },
                        )
                    )
                    writer_published.set()
                except BaseException as exc:
                    thread_errors.append(exc)

            with workspace.materialize_page_working_set(base) as working_set:
                with (
                    mock.patch.object(
                        workspace,
                        "read_pending_artifact_set",
                        side_effect=pause_after_cleanup_reads_obsolete_pending,
                    ),
                    mock.patch.object(
                        workspace,
                        "clear_pending_artifact_set",
                        side_effect=clear_only_after_concurrent_publication,
                    ),
                    mock.patch.object(
                        workspace,
                        "_head_commit_lock",
                        side_effect=observe_project_lock,
                    ),
                ):
                    page_committer = threading.Thread(
                        target=commit_page,
                        args=(working_set,),
                        name="page-commit",
                        daemon=True,
                    )
                    page_committer.start()
                    self.assertTrue(stale_read_reached.wait(timeout=5))

                    pending_writer = threading.Thread(
                        target=publish_current_pending,
                        name="current-pending-writer",
                        daemon=True,
                    )
                    pending_writer.start()
                    self.assertTrue(writer_lock_attempted.wait(timeout=5))
                    allow_stale_read_to_return.set()

                    page_committer.join(timeout=5)
                    pending_writer.join(timeout=5)

            self.assertFalse(page_committer.is_alive())
            self.assertFalse(pending_writer.is_alive())
            self.assertEqual(thread_errors, [])
            self.assertEqual(len(committed_results), 1)
            self.assertEqual(len(concurrent_pending), 1)
            self.assertTrue(pending_path.is_file())
            retained_pending = workspace.read_pending_artifact_set(project_id)
            self.assertEqual(
                retained_pending["resume_fingerprint"],
                "concurrent-current-head",
            )
            blob_id = concurrent_pending[0]["artifact_bundle"]["files"][
                "translated/concurrent.bin"
            ]["blob"]
            blob_path = (
                workspace.project_artifact_store_dir(project_id)
                / blob_id[:2]
                / blob_id
            )
            self.assertTrue(blob_path.is_file())

    def test_artifact_gc_cannot_delete_a_blob_before_pending_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.make_workspace(root)
            project_id = "project-a"
            artifact = root / "pending-output.bin"
            artifact.write_bytes(b"new pending artifact")
            pending_path = workspace.project_pending_artifact_path(project_id)
            publication_reached = threading.Event()
            allow_publication = threading.Event()
            gc_lock_attempted = threading.Event()
            thread_errors: list[BaseException] = []
            original_write_json = workspace.write_json_file
            project_lock = workspace._head_commit_lock(project_id)

            def pause_before_pending_publication(
                path: Path,
                payload: dict[str, object],
            ) -> None:
                if (
                    threading.current_thread().name == "pending-writer"
                    and Path(path) == pending_path
                ):
                    publication_reached.set()
                    if not allow_publication.wait(timeout=5):
                        raise RuntimeError("pending publication was not released")
                original_write_json(path, payload)

            def observe_project_lock(requested_project_id: str):
                if threading.current_thread().name == "artifact-gc":
                    gc_lock_attempted.set()
                return project_lock

            def write_pending() -> None:
                try:
                    workspace.write_pending_artifact_set(
                        project_id,
                        action="rerender",
                        resume_fingerprint="concurrent-pending",
                        base_head=None,
                        state_document={
                            "schema_version": 2,
                            "project_id": project_id,
                            "source_images": [],
                            "workflow_stage": "translated",
                            "artifact_state": {"schema_version": 2, "pages": {}},
                        },
                        files={"translated/output.bin": artifact},
                    )
                except BaseException as exc:
                    thread_errors.append(exc)

            def collect_artifacts() -> None:
                try:
                    workspace.garbage_collect_snapshot_blobs(project_id)
                except BaseException as exc:
                    thread_errors.append(exc)

            self.addCleanup(allow_publication.set)
            with (
                mock.patch.object(
                    workspace,
                    "write_json_file",
                    side_effect=pause_before_pending_publication,
                ),
                mock.patch.object(
                    workspace,
                    "_head_commit_lock",
                    side_effect=observe_project_lock,
                ),
            ):
                writer = threading.Thread(
                    target=write_pending,
                    name="pending-writer",
                    daemon=True,
                )
                writer.start()
                self.assertTrue(publication_reached.wait(timeout=5))
                blob_files = list(
                    workspace.project_artifact_store_dir(project_id).glob(
                        "[0-9a-f][0-9a-f]/*"
                    )
                )
                self.assertEqual(len(blob_files), 1)

                collector = threading.Thread(
                    target=collect_artifacts,
                    name="artifact-gc",
                    daemon=True,
                )
                collector.start()
                self.assertTrue(gc_lock_attempted.wait(timeout=5))

                allow_publication.set()
                writer.join(timeout=5)
                self.assertFalse(writer.is_alive())
                collector.join(timeout=5)
                self.assertFalse(collector.is_alive())

            self.assertEqual(thread_errors, [])
            pending = workspace.read_pending_artifact_set(project_id)
            self.assertIsNotNone(pending)
            self.assertTrue(blob_files[0].is_file())

    def test_artifact_gc_cannot_delete_a_blob_before_head_publication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.make_workspace(root)
            project_id = "project-a"
            capture_reached = threading.Event()
            allow_publication = threading.Event()
            gc_lock_attempted = threading.Event()
            thread_errors: list[BaseException] = []
            original_capture = workspace.capture_snapshot_artifacts
            project_lock = workspace._head_commit_lock(project_id)

            def pause_after_head_capture(
                requested_project_id: str,
                files: dict[str, Path],
                previous_bundle: dict[str, object] | None = None,
            ) -> dict[str, object]:
                captured = original_capture(
                    requested_project_id,
                    files,
                    previous_bundle=previous_bundle,
                )
                if threading.current_thread().name == "head-writer":
                    capture_reached.set()
                    if not allow_publication.wait(timeout=5):
                        raise RuntimeError("Head publication was not released")
                return captured

            def observe_project_lock(requested_project_id: str):
                if threading.current_thread().name == "head-artifact-gc":
                    gc_lock_attempted.set()
                return project_lock

            def commit_head() -> None:
                try:
                    workspace.commit_project_head(
                        project_id,
                        state_document={
                            "schema_version": 2,
                            "project_id": project_id,
                            "source_images": [],
                        },
                        project_manifest={
                            "project_id": project_id,
                            "title": "Concurrent Head",
                        },
                        page_documents={},
                    )
                except BaseException as exc:
                    thread_errors.append(exc)

            def collect_artifacts() -> None:
                try:
                    workspace.garbage_collect_snapshot_blobs(project_id)
                except BaseException as exc:
                    thread_errors.append(exc)

            self.addCleanup(allow_publication.set)
            with (
                mock.patch.object(
                    workspace,
                    "capture_snapshot_artifacts",
                    side_effect=pause_after_head_capture,
                ),
                mock.patch.object(
                    workspace,
                    "_head_commit_lock",
                    side_effect=observe_project_lock,
                ),
            ):
                writer = threading.Thread(
                    target=commit_head,
                    name="head-writer",
                    daemon=True,
                )
                writer.start()
                self.assertTrue(capture_reached.wait(timeout=5))
                blob_files = list(
                    workspace.project_artifact_store_dir(project_id).glob(
                        "[0-9a-f][0-9a-f]/*"
                    )
                )
                self.assertGreater(len(blob_files), 0)

                collector = threading.Thread(
                    target=collect_artifacts,
                    name="head-artifact-gc",
                    daemon=True,
                )
                collector.start()
                self.assertTrue(gc_lock_attempted.wait(timeout=5))

                allow_publication.set()
                writer.join(timeout=5)
                self.assertFalse(writer.is_alive())
                collector.join(timeout=5)
                self.assertFalse(collector.is_alive())

            self.assertEqual(thread_errors, [])
            head = workspace.read_project_head(project_id)
            self.assertIsNotNone(head)
            workspace.materialize_project_head_artifact(
                project_id,
                "state/session.json",
                root / "restored-session.json",
            )

    def test_pending_v2_rejects_invalid_or_regressive_checkpoint_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.make_workspace(root)
            state_document, files = self.make_pending_checkpoint_evidence(root)
            page_id = "001.png"
            common = {
                "project_id": "project-a",
                "action": "resume-translate",
                "resume_fingerprint": "checkpoint-monotonicity",
                "base_head": None,
                "state_document": state_document,
            }
            workspace.write_pending_artifact_set(
                **common,
                files=files,
                metadata={"page_checkpoints": {page_id: "finalized"}},
            )
            pending_path = workspace.project_pending_artifact_path("project-a")
            valid_evidence = pending_path.read_bytes()

            invalid_cases = (
                (
                    "unknown-stage",
                    {
                        **common,
                        "files": files,
                        "metadata": {
                            "page_checkpoints": {page_id: "future-stage"}
                        },
                    },
                ),
                (
                    "unsafe-page-id",
                    {
                        **common,
                        "files": files,
                        "metadata": {
                            "page_checkpoints": {"../outside.png": "finalized"}
                        },
                    },
                ),
                (
                    "illegal-action-stage",
                    {
                        **common,
                        "action": "detect",
                        "files": files,
                        "metadata": {"page_checkpoints": {}},
                    },
                ),
                (
                    "finalized-without-rendered-evidence",
                    {
                        **common,
                        "files": {
                            path: file_path
                            for path, file_path in files.items()
                            if not path.startswith("translated/")
                        },
                        "metadata": {
                            "page_checkpoints": {page_id: "finalized"}
                        },
                    },
                ),
                (
                    "stage-regression",
                    {
                        **common,
                        "files": files,
                        "metadata": {
                            "page_checkpoints": {page_id: "rendered"}
                        },
                    },
                ),
                (
                    "page-removal",
                    {
                        **common,
                        "files": files,
                        "metadata": {"page_checkpoints": {}},
                    },
                ),
            )
            for label, kwargs in invalid_cases:
                with self.subTest(case=label):
                    with self.assertRaises(CorruptProjectArtifactError):
                        workspace.write_pending_artifact_set(**kwargs)
                    self.assertEqual(pending_path.read_bytes(), valid_evidence)

    def test_project_working_set_exposes_immutable_normalized_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.make_workspace(root)
            state_document, files = self.make_pending_checkpoint_evidence(root)
            head = workspace.commit_project_head(
                "project-a",
                state_document=state_document,
                project_manifest={"project_id": "project-a", "title": "Project A"},
                page_documents={
                    "001.png": {
                        "page_id": "001.png",
                        "metadata": {"revision": 1},
                        "regions": [],
                    }
                },
                artifact_files=files,
            )
            workspace.write_pending_artifact_set(
                "project-a",
                action="resume-translate",
                resume_fingerprint="immutable-checkpoints",
                base_head=head,
                state_document=state_document,
                files=files,
                metadata={
                    "page_checkpoints": {"001.png": "finalized"}
                },
            )
            base = workspace.read_project_command_base("project-a")

            with workspace.materialize_project_working_set(
                base,
                action="resume-translate",
                resume_fingerprint="immutable-checkpoints",
            ) as working_set:
                self.assertEqual(
                    dict(working_set.page_checkpoints),
                    {"001.png": "finalized"},
                )
                with self.assertRaises(TypeError):
                    working_set.page_checkpoints["001.png"] = "rendered"  # type: ignore[index]

    def test_pending_v2_tampering_fails_closed_without_rewriting_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.make_workspace(root)
            state_document, files = self.make_pending_checkpoint_evidence(root)
            workspace.write_pending_artifact_set(
                "project-a",
                action="resume-translate",
                resume_fingerprint="tamper-evidence",
                base_head=None,
                state_document=state_document,
                files=files,
                metadata={
                    "page_checkpoints": {"001.png": "finalized"}
                },
            )
            pending_path = workspace.project_pending_artifact_path("project-a")
            valid_pending = workspace.read_json_file(pending_path, {})

            for corruption in (
                "unknown-stage",
                "unsafe-page-id",
                "illegal-action-stage",
                "missing-rendered-evidence",
                "mixed-legacy-field",
                "boolean-schema",
            ):
                with self.subTest(corruption=corruption):
                    pending = copy.deepcopy(valid_pending)
                    if corruption == "unknown-stage":
                        pending["page_checkpoints"]["001.png"] = "future-stage"
                    elif corruption == "unsafe-page-id":
                        pending["page_checkpoints"] = {
                            "../outside.png": "finalized"
                        }
                    elif corruption == "illegal-action-stage":
                        pending["action"] = "detect"
                    elif corruption == "missing-rendered-evidence":
                        pending["artifact_bundle"]["files"].pop(
                            "translated/001.png"
                        )
                    elif corruption == "mixed-legacy-field":
                        pending["completed_page_ids"] = ["001.png"]
                    else:
                        pending["schema_version"] = True
                    workspace.write_json_file(pending_path, pending)
                    diagnostic_evidence = pending_path.read_bytes()

                    with self.assertRaises(CorruptProjectArtifactError):
                        workspace.read_pending_artifact_set("project-a")

                    self.assertEqual(
                        pending_path.read_bytes(),
                        diagnostic_evidence,
                    )
                    workspace.write_json_file(pending_path, valid_pending)

    def test_snapshot_artifacts_reject_traversal_and_bad_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.make_workspace(root)
            source = root / "artifact.txt"
            source.write_text("safe", encoding="utf-8")

            with self.assertRaises(CorruptSnapshotArtifactError):
                workspace.capture_snapshot_artifacts("project-a", {"../outside.txt": source})
            with self.assertRaises(CorruptSnapshotArtifactError):
                workspace.restore_snapshot_artifacts(
                    "project-a",
                    {
                        "schema_version": 1,
                        "files": {"source/page.txt": {"blob": "not-a-hash"}},
                    },
                    {"source": root / "restored"},
                )

    def test_project_index_is_rebuilt_from_manifests_instead_of_trusting_stale_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            workspace.write_project_index(
                [{"project_id": "ghost", "updated_at": "2099-01-01"}]
            )
            workspace.write_json_file(
                workspace.project_manifest_path("project-a"),
                {
                    "project_id": "project-a",
                    "title": "Project A",
                    "updated_at": "2026-01-01",
                    "source_dir": "/private/project-a/source",
                    "translated_dir": "/private/project-a/translated",
                },
            )
            workspace.write_json_file(
                workspace.project_manifest_path("project-b"),
                {
                    "project_id": "project-b",
                    "title": "Project B",
                    "updated_at": "2026-01-02",
                },
            )

            rebuilt = workspace.rebuild_project_index()

            self.assertEqual(
                [item["project_id"] for item in rebuilt],
                ["project-b", "project-a"],
            )
            self.assertEqual(
                workspace.read_json_file(workspace.project_index_path, []),
                rebuilt,
            )
            self.assertNotIn("source_dir", rebuilt[1])
            self.assertNotIn("translated_dir", rebuilt[1])

    def test_project_index_rebuild_derives_empty_snapshot_summary_from_catalog(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            workspace.write_json_file(
                workspace.project_manifest_path("project-a"),
                {
                    "project_id": "project-a",
                    "title": "Project A",
                    "updated_at": "2026-01-01",
                    "latest_snapshot_id": "dangling",
                    "latest_snapshot_kind": "stale-kind",
                    "latest_snapshot_summary": "stale summary",
                    "snapshot_count": 99,
                },
            )

            rebuilt = workspace.rebuild_project_index()

            self.assertEqual(rebuilt[0]["snapshot_count"], 0)
            self.assertEqual(rebuilt[0]["latest_snapshot_id"], "")
            self.assertEqual(rebuilt[0]["latest_snapshot_kind"], "")
            self.assertEqual(rebuilt[0]["latest_snapshot_summary"], "")

    def test_corrupt_snapshot_catalog_rebuild_preserves_last_valid_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = self.make_workspace(Path(tmp))
            valid_index = [{"project_id": "last-valid", "snapshot_count": 3}]
            workspace.write_project_index(valid_index)
            workspace.write_json_file(
                workspace.project_manifest_path("project-a"),
                {
                    "project_id": "project-a",
                    "title": "Project A",
                    "updated_at": "2026-01-01",
                },
            )
            snapshots_dir = workspace.project_snapshots_dir("project-a")
            snapshots_dir.mkdir(parents=True)
            (snapshots_dir / "broken.json").write_text("{bad", encoding="utf-8")

            with self.assertRaises(CorruptProjectArtifactError):
                workspace.rebuild_project_index()

            self.assertEqual(
                workspace.read_json_file(workspace.project_index_path, []),
                valid_index,
            )


if __name__ == "__main__":
    unittest.main()
