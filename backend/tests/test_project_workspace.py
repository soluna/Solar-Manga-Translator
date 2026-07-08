from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from engine.project_workspace import InvalidStorageIdentifierError, ProjectWorkspace
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


if __name__ == "__main__":
    unittest.main()
