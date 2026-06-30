from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import runtime_paths as runtime_paths_module
from runtime_paths import AppPaths


def make_paths(root: Path) -> AppPaths:
    app_data_dir = root / "Solar-Manga-Translator"
    return AppPaths(
        code_dir=root / "repo" / "backend",
        app_data_dir=app_data_dir,
        models_dir=app_data_dir / "models",
        output_dir=app_data_dir / "output",
        logs_dir=app_data_dir / "logs",
        cache_dir=app_data_dir / "cache",
        config_dir=app_data_dir / "config",
    )


class RuntimePathsTests(unittest.TestCase):
    def write_project(self, projects_dir: Path, project_id: str, updated_at: str = "2026-06-29T00:00:00Z") -> None:
        (projects_dir / project_id).mkdir(parents=True)
        (projects_dir / project_id / "project.json").write_text(
            json.dumps({
                "project_id": project_id,
                "title": project_id,
                "updated_at": updated_at,
            }),
            encoding="utf-8",
        )
        (projects_dir / "project_index.json").write_text(
            json.dumps([{
                "project_id": project_id,
                "title": project_id,
                "updated_at": updated_at,
            }]),
            encoding="utf-8",
        )

    def test_migrate_legacy_app_data_from_old_app_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_paths(root)
            legacy_dir = root / "MangaTranslator"
            legacy_projects_dir = legacy_dir / "projects"
            legacy_output_dir = legacy_dir / "output" / "project-a"
            legacy_fonts_dir = legacy_dir / "fonts"
            legacy_models_dir = legacy_dir / "models" / "inpainting"
            legacy_settings_path = legacy_dir / "config" / "settings.json"

            self.write_project(legacy_projects_dir, "project-a")
            legacy_output_dir.mkdir(parents=True)
            (legacy_output_dir / "page-1.png").write_bytes(b"image")
            legacy_fonts_dir.mkdir(parents=True)
            (legacy_fonts_dir / "custom.otf").write_bytes(b"font")
            legacy_models_dir.mkdir(parents=True)
            (legacy_models_dir / "lama.ckpt").write_bytes(b"model")
            legacy_settings_path.parent.mkdir(parents=True)
            legacy_settings_path.write_text(json.dumps({"translator": "doubao-ark"}), encoding="utf-8")

            with mock.patch.object(runtime_paths_module, "_platform_app_data_bases", return_value=[root]):
                status = paths.legacy_status()
                self.assertTrue(status["needed"])
                self.assertTrue(status["summary"]["has_legacy_app_projects"])

                migrated = paths.migrate_legacy("migrate")

            self.assertEqual(migrated["status"], "completed")
            self.assertFalse(migrated["needed"])
            self.assertTrue((paths.projects_dir / "project-a" / "project.json").exists())
            self.assertTrue((paths.output_dir / "project-a" / "page-1.png").exists())
            self.assertTrue((paths.user_fonts_dir / "custom.otf").exists())
            self.assertTrue((paths.models_dir / "inpainting" / "lama.ckpt").exists())
            self.assertEqual(paths.load_settings()["translator"], "doubao-ark")
            project_index = json.loads(paths.project_index_path.read_text(encoding="utf-8"))
            self.assertEqual(project_index[0]["project_id"], "project-a")

    def test_legacy_status_reprompts_when_completed_state_missed_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_paths(root)
            legacy_projects_dir = root / "MangaTranslator" / "projects"
            self.write_project(legacy_projects_dir, "old-project")
            paths.save_migration_state({"status": "completed", "updated_at": "2026-06-30T00:00:00Z"})
            self.write_project(paths.projects_dir, "new-project")

            with mock.patch.object(runtime_paths_module, "_platform_app_data_bases", return_value=[root]):
                status = paths.legacy_status()

            self.assertTrue(status["needed"])
            self.assertTrue(status["summary"]["has_unmigrated_projects"])


if __name__ == "__main__":
    unittest.main()
