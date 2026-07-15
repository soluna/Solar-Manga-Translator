from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from engine.project_workspace import (
    CorruptSnapshotArtifactError,
    InvalidStorageIdentifierError,
    ProjectHeadConflictError,
    ProjectWorkspace,
)
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
                committed_head = workspace.commit_project_head(
                    project_id,
                    state_document=state_document,
                    project_manifest=project_manifest,
                    page_documents={"001.png": {"page_id": "001.png", "metadata": {"revision": 1}}},
                )

            self.assertEqual(workspace.read_project_head(project_id), committed_head)
            self.assertEqual(
                workspace.read_project_session_document(project_id),
                state_document,
            )
            self.assertFalse(workspace.project_session_state_path(project_id).exists())

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

            workspace.garbage_collect_snapshot_blobs("project-a", [{"artifact_bundle": bundle}])
            self.assertTrue(blob_files[0].exists())
            workspace.garbage_collect_snapshot_blobs("project-a", [])
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
            snapshot = {
                "project_head_revision_id": first_head["revision_id"],
                "artifact_bundle": {
                    "schema_version": 1,
                    "files": first_head["files"],
                },
            }

            workspace.garbage_collect_snapshot_blobs(project_id, [snapshot])

            self.assertTrue(first_revision_path.exists())
            self.assertTrue(second_revision_path.exists())
            self.assertTrue(first_blob_path.exists())
            self.assertTrue(second_blob_path.exists())

            workspace.garbage_collect_snapshot_blobs(project_id, [])

            self.assertFalse(first_revision_path.exists())
            self.assertTrue(second_revision_path.exists())
            self.assertFalse(first_blob_path.exists())
            self.assertTrue(second_blob_path.exists())

    def test_pending_artifact_set_shares_the_store_and_is_a_gc_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = self.make_workspace(root)
            pending_output = root / "pending-page.png"
            pending_output.write_bytes(b"completed pending page")

            pending = workspace.write_pending_artifact_set(
                "project-a",
                action="resume_translation",
                resume_fingerprint="fingerprint-a",
                base_head=None,
                state_document={"schema_version": 2, "project_id": "project-a"},
                files={"translated/001.png": pending_output},
            )
            blob_id = pending["artifact_bundle"]["files"]["translated/001.png"]["blob"]
            blob_path = workspace.project_artifact_store_dir("project-a") / blob_id[:2] / blob_id
            restored_dir = root / "restored"

            workspace.garbage_collect_snapshot_blobs("project-a", [])
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
            workspace.garbage_collect_snapshot_blobs("project-a", [])

            self.assertIsNone(workspace.read_pending_artifact_set("project-a"))
            self.assertFalse(blob_path.exists())

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


if __name__ == "__main__":
    unittest.main()
