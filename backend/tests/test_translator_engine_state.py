from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from engine.translator import TranslatorEngine
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


class TranslatorEngineStateTests(unittest.TestCase):
    def make_engine(self, root: Path) -> TranslatorEngine:
        return TranslatorEngine(BACKEND_DIR, app_paths=make_test_paths(root))

    def test_busy_mark_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            self.assertTrue(engine.try_mark_session_busy("project-a", "translate"))
            self.assertFalse(engine.try_mark_session_busy("project-a", "rerender"))
            self.assertTrue(engine.is_session_busy("project-a"))

            engine.clear_session_busy("project-a")
            self.assertFalse(engine.is_session_busy("project-a"))
            self.assertTrue(engine.try_mark_session_busy("project-a", "rerender"))

    def test_page_commands_reject_unknown_region_without_dirty_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            session = {
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "translation_region_overrides": {},
                "translation_region_layout_overrides": {},
                "last_config": {},
            }
            engine._page_document_region_ids = lambda *_args, **_kwargs: {"known-region"}  # type: ignore[method-assign]

            with self.assertRaises(FileNotFoundError):
                asyncio.run(engine.apply_page_commands(
                    project_id="project-a",
                    session=session,
                    page_id="page-1.png",
                    raw_config={},
                    commands=[{
                        "type": "update_translation",
                        "region_id": "missing-region",
                        "text": "should not persist",
                    }],
                ))

            self.assertNotIn("missing-region", session["translation_region_overrides"])
            self.assertNotIn("missing-region", session["translation_region_layout_overrides"])

    def test_restore_rejects_project_when_all_source_images_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            project_id = "broken-project"
            state = {
                "project_id": project_id,
                "source_dir": str(Path(tmp) / "missing-source"),
                "translated_dir": str(Path(tmp) / "missing-translated"),
                "source_images": [{"name": "gone.png", "stored_name": "gone.png"}],
                "workflow_stage": "translated",
                "translated_output_map": {"gone.png": "gone.png"},
            }
            engine._write_json_file(engine._project_session_state_path(project_id), state)
            engine._write_json_file(engine._project_manifest_path(project_id), {"project_id": project_id})

            with self.assertRaises(FileNotFoundError):
                engine.restore_project_session(project_id)


if __name__ == "__main__":
    unittest.main()
