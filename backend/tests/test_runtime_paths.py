from __future__ import annotations

import json
import os
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
    app_data_dir = root / "repo" / ".runtime"
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

    def test_default_app_data_lives_under_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "manga-translator"
            backend_dir = project_root / "backend"
            backend_dir.mkdir(parents=True)

            with mock.patch.dict(os.environ, {}, clear=True):
                paths = runtime_paths_module.resolve_app_paths(backend_dir)

            self.assertEqual(paths.app_data_dir, (project_root / ".runtime").resolve())
            self.assertEqual(paths.models_dir, (project_root / ".runtime" / "models").resolve())
            self.assertTrue(paths.projects_dir.is_dir())

    def test_app_data_environment_override_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            configured_data_dir = root / "custom-data"

            with mock.patch.dict(
                os.environ,
                {"APP_DATA_DIR": str(configured_data_dir)},
                clear=True,
            ):
                paths = runtime_paths_module.resolve_app_paths(root / "repo" / "backend")

            self.assertEqual(paths.app_data_dir, configured_data_dir.resolve())

    def test_legacy_per_directory_overrides_cannot_split_the_unified_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            configured_data_dir = root / "portable-data"

            with mock.patch.dict(
                os.environ,
                {
                    "APP_DATA_DIR": str(configured_data_dir),
                    "APP_MODELS_DIR": str(root / "old-models"),
                    "APP_OUTPUT_DIR": str(root / "old-output"),
                    "APP_LOG_DIR": str(root / "old-logs"),
                },
                clear=True,
            ):
                paths = runtime_paths_module.resolve_app_paths(root / "repo" / "backend")

            self.assertEqual(paths.models_dir, configured_data_dir.resolve() / "models")
            self.assertEqual(paths.output_dir, configured_data_dir.resolve() / "output")
            self.assertEqual(paths.logs_dir, configured_data_dir.resolve() / "logs")

    def test_runtime_environment_routes_temp_and_external_caches_under_app_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_temp = root / "legacy-system-temp"
            legacy_temp.mkdir()
            bundled_fonts = root / "repo" / "fonts" / "system"
            bundled_fonts.mkdir(parents=True)
            (bundled_fonts / "bundled.otf").write_bytes(b"font")
            previous_tempdir = tempfile.tempdir
            try:
                with mock.patch.dict(
                    os.environ,
                    {
                        "TEMP": str(legacy_temp),
                        "TMP": str(legacy_temp),
                        "HF_HOME": str(root / "old-huggingface-cache"),
                    },
                    clear=True,
                ):
                    paths = runtime_paths_module.resolve_app_paths(root / "repo" / "backend")
                    environment = runtime_paths_module.configure_runtime_environment(paths)

                    self.assertEqual(paths.temp_dir, paths.app_data_dir / "temp")
                    self.assertTrue(paths.temp_dir.is_dir())
                    self.assertEqual(environment["TEMP"], str(paths.temp_dir))
                    self.assertEqual(environment["TMP"], str(paths.temp_dir))
                    self.assertEqual(environment["TMPDIR"], str(paths.temp_dir))
                    self.assertEqual(environment["HF_HOME"], str(paths.cache_dir / "huggingface"))
                    self.assertEqual(environment["TORCH_HOME"], str(paths.cache_dir / "torch"))
                    self.assertEqual(environment["CUDA_CACHE_PATH"], str(paths.cache_dir / "cuda"))
                    self.assertEqual(environment["APP_LEGACY_TEMP_DIRS"], str(legacy_temp.resolve()))
                    self.assertEqual(Path(tempfile.gettempdir()), paths.temp_dir)
                    self.assertTrue(
                        (paths.user_fonts_dir / "system" / "bundled.otf").exists(),
                    )
                    for name, configured_path in environment.items():
                        if name == "APP_LEGACY_TEMP_DIRS":
                            continue
                        resolved_path = Path(configured_path).resolve()
                        self.assertTrue(
                            resolved_path == paths.app_data_dir
                            or paths.app_data_dir in resolved_path.parents,
                            f"{name} escaped runtime root: {configured_path}",
                        )
            finally:
                tempfile.tempdir = previous_tempdir

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
            self.assertTrue((paths.user_fonts_dir / "custom" / "custom.otf").exists())
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

    def test_skip_suppresses_migration_prompt_until_app_version_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_paths(root)
            legacy_projects_dir = root / "MangaTranslator" / "projects"
            self.write_project(legacy_projects_dir, "old-project")

            with (
                mock.patch.object(runtime_paths_module, "_platform_app_data_bases", return_value=[root]),
                mock.patch.dict(os.environ, {"APP_VERSION": "1.2.3"}, clear=False),
            ):
                skipped = paths.migrate_legacy("skip")
                self.assertFalse(skipped["needed"])
                self.assertEqual(paths.load_migration_state()["version"], "1.2.3")

            with (
                mock.patch.object(runtime_paths_module, "_platform_app_data_bases", return_value=[root]),
                mock.patch.dict(os.environ, {"APP_VERSION": "1.3.0"}, clear=False),
            ):
                status_after_upgrade = paths.legacy_status()

            self.assertTrue(status_after_upgrade["needed"])

    def test_migrate_canonical_app_name_from_alternate_windows_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_paths(root / "Local")
            roaming_dir = root / "Roaming" / runtime_paths_module.APP_NAME
            self.write_project(roaming_dir / "projects", "roaming-project")

            with mock.patch.object(
                runtime_paths_module,
                "_platform_app_data_bases",
                return_value=[root / "Local", root / "Roaming"],
            ):
                status = paths.legacy_status()
                self.assertTrue(status["needed"])
                migrated = paths.migrate_legacy("migrate")

            self.assertFalse(migrated["needed"])
            self.assertTrue(
                (paths.projects_dir / "roaming-project" / "project.json").exists(),
            )

    def test_migrate_and_clean_preserves_projects_then_removes_only_app_owned_legacy_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_paths(root / "install")
            legacy_app_dir = root / "LocalAppData" / runtime_paths_module.APP_NAME
            self.write_project(legacy_app_dir / "projects", "legacy-project")
            (legacy_app_dir / "models" / "ocr").mkdir(parents=True)
            (legacy_app_dir / "models" / "ocr" / "model.ckpt").write_bytes(b"model")

            system_temp = root / "system-temp"
            app_temp_cache = system_temp / "manga-image-translator"
            app_temp_cache.mkdir(parents=True)
            (app_temp_cache / "cache.bin").write_bytes(b"cache")
            unrelated_temp = system_temp / "keep-me.txt"
            unrelated_temp.write_text("unrelated", encoding="utf-8")

            with (
                mock.patch.object(
                    runtime_paths_module,
                    "_platform_app_data_bases",
                    return_value=[root / "LocalAppData"],
                ),
                mock.patch.dict(
                    os.environ,
                    {"APP_LEGACY_TEMP_DIRS": str(system_temp)},
                    clear=False,
                ),
            ):
                status = paths.legacy_status()
                self.assertTrue(status["summary"]["has_legacy_temp_cache"])
                self.assertIn(str(app_temp_cache), status["legacy"]["temp_cache"])
                migrated = paths.migrate_legacy("migrate_clean")

            self.assertEqual(migrated["status"], "completed")
            self.assertEqual(migrated["cleanup"]["status"], "completed")
            self.assertTrue(
                (paths.projects_dir / "legacy-project" / "project.json").exists(),
            )
            self.assertTrue((paths.models_dir / "ocr" / "model.ckpt").exists())
            self.assertFalse(legacy_app_dir.exists())
            self.assertFalse(app_temp_cache.exists())
            self.assertTrue(unrelated_temp.exists())

    def test_old_electron_cache_without_projects_is_detected_and_can_be_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_paths(root / "install")
            legacy_app_dir = root / "Roaming" / runtime_paths_module.APP_NAME
            chromium_cache = legacy_app_dir / "Cache" / "Cache_Data"
            chromium_cache.mkdir(parents=True)
            (chromium_cache / "data_0").write_bytes(b"electron-cache")

            with mock.patch.object(
                runtime_paths_module,
                "_platform_app_data_bases",
                return_value=[root / "Roaming"],
            ):
                status = paths.legacy_status()
                self.assertTrue(status["needed"])
                self.assertTrue(status["summary"]["has_legacy_app_runtime"])
                self.assertGreaterEqual(
                    status["summary"]["legacy_bytes"],
                    len(b"electron-cache"),
                )
                migrated = paths.migrate_legacy("migrate_clean")

            self.assertEqual(migrated["cleanup"]["status"], "completed")
            self.assertFalse(legacy_app_dir.exists())

    def test_migrate_and_clean_archives_conflicting_legacy_settings_before_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_paths(root / "install")
            paths.save_settings({"translator": "current"})
            legacy_app_dir = root / "LocalAppData" / runtime_paths_module.APP_NAME
            legacy_settings = legacy_app_dir / "config" / "settings.json"
            legacy_settings.parent.mkdir(parents=True)
            legacy_settings.write_text(
                json.dumps({"translator": "legacy"}),
                encoding="utf-8",
            )

            with mock.patch.object(
                runtime_paths_module,
                "_platform_app_data_bases",
                return_value=[root / "LocalAppData"],
            ):
                paths.migrate_legacy("migrate_clean")

            self.assertEqual(paths.load_settings()["translator"], "current")
            archived_settings = list(
                (paths.config_dir / "legacy-settings").glob("*.json"),
            )
            self.assertEqual(len(archived_settings), 1)
            self.assertEqual(
                json.loads(archived_settings[0].read_text(encoding="utf-8"))["translator"],
                "legacy",
            )
            self.assertFalse(legacy_app_dir.exists())

    def test_migration_keeps_source_checkout_custom_fonts_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = make_paths(root)
            legacy_custom_fonts = paths.code_dir.parent / "fonts" / "custom"
            legacy_custom_fonts.mkdir(parents=True)
            (legacy_custom_fonts / "comic.otf").write_bytes(b"font")

            status = paths.legacy_status()
            self.assertTrue(status["summary"]["has_legacy_project_fonts"])
            migrated = paths.migrate_legacy("migrate")

            self.assertEqual(migrated["status"], "completed")
            self.assertTrue(
                (paths.user_fonts_dir / "custom" / "comic.otf").exists(),
            )
            self.assertTrue((legacy_custom_fonts / "comic.otf").exists())


if __name__ == "__main__":
    unittest.main()
