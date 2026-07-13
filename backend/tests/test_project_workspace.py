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
