from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image


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

    def test_openai_compatible_settings_validation_uses_lightweight_http_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            async def fail_vendor_translation(*_args, **_kwargs):
                raise AssertionError("settings validation should not import the full translator dispatcher")

            def fake_validation_request(**kwargs):
                self.assertEqual(kwargs["base_url"], "https://api.example.com/v1")
                self.assertEqual(kwargs["model"], "example-model")
                self.assertEqual(kwargs["api_key"], "secret")
                return "测试"

            engine._translate_text_batch = fail_vendor_translation  # type: ignore[method-assign]
            engine._request_chat_completions_validation_sync = fake_validation_request  # type: ignore[method-assign]

            result = asyncio.run(engine.validate_user_config({
                "translator": "openai-compatible",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "example-model",
                "api_key": "secret",
            }))

            self.assertTrue(result.get("ok"))
            self.assertEqual(result.get("preview"), "测试")
            self.assertEqual(result.get("translator"), "openai-compatible")

    def test_openai_compatible_settings_validation_requires_base_url_and_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            missing_base_url = asyncio.run(engine.validate_user_config({
                "translator": "openai-compatible",
                "openai_model": "example-model",
                "api_key": "secret",
            }))
            self.assertFalse(missing_base_url.get("ok"))
            self.assertIn("API Base URL", str(missing_base_url.get("message")))

            missing_model = asyncio.run(engine.validate_user_config({
                "translator": "openai-compatible",
                "openai_base_url": "https://api.example.com/v1",
                "api_key": "secret",
            }))
            self.assertFalse(missing_model.get("ok"))
            self.assertIn("模型名称", str(missing_model.get("message")))

    def test_rerender_imports_avoid_vendor_utils_aggregate(self) -> None:
        render_files = [
            BACKEND_DIR / "patched_manga_translator_init.py",
            BACKEND_DIR / "patched_utils_init.py",
            BACKEND_DIR / "patched_rendering_init.py",
            BACKEND_DIR / "patched_text_render.py",
        ]

        for render_file in render_files:
            with self.subTest(render_file=render_file.name):
                content = render_file.read_text(encoding="utf-8")
                self.assertNotIn("from ..utils import", content)

    def test_rendering_import_does_not_load_inference_stack(self) -> None:
        vendor_root = BACKEND_DIR / "manga-image-translator" / "manga_translator"
        if not vendor_root.exists():
            self.skipTest("manga-image-translator vendor checkout is not installed")

        script = """
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path("backend/manga-image-translator").resolve()))
sys.path.insert(0, str(Path("backend").resolve()))
from patch_pydensecrf import patch_mask_refinement

if not patch_mask_refinement():
    raise SystemExit("runtime patch failed")

import manga_translator.rendering

print(json.dumps({
    "onnxruntime": "onnxruntime" in sys.modules,
    "torch": "torch" in sys.modules,
    "utils_inference": "manga_translator.utils.inference" in sys.modules,
    "full_translator": "manga_translator.manga_translator" in sys.modules,
}, sort_keys=True))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(BACKEND_DIR.parent),
            text=True,
            capture_output=True,
            check=True,
        )
        loaded_modules = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(
            loaded_modules,
            {
                "onnxruntime": False,
                "torch": False,
                "utils_inference": False,
                "full_translator": False,
            },
        )

    def test_vertical_renderer_columns_are_top_aligned(self) -> None:
        render_files = [
            BACKEND_DIR / "patched_text_render.py",
        ]
        vendor_text_render = BACKEND_DIR / "manga-image-translator" / "manga_translator" / "rendering" / "text_render.py"
        if vendor_text_render.exists():
            render_files.append(vendor_text_render)

        for render_file in render_files:
            with self.subTest(render_file=render_file.name):
                content = render_file.read_text(encoding="utf-8")
                self.assertNotIn("pen_line[1] += (max(line_height_list) - line_height) // 2", content)
                self.assertNotIn("pen_line[1] += max(line_height_list) - line_height", content)

    def test_rerender_result_image_preserves_source_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_path = root / "source.png"
            Image.new("RGBA", (2, 2), (255, 255, 255, 0)).save(source_path)

            rendered_rgb = np.full((2, 2, 3), [12, 34, 56], dtype=np.uint8)
            result_image = engine._rendered_rgb_to_pil_image(source_path, rendered_rgb)

            self.assertEqual(result_image.mode, "RGBA")
            self.assertEqual(np.asarray(result_image.getchannel("A")).reshape(-1).tolist(), [0, 0, 0, 0])
            output_path = root / "nested" / "result.png"
            engine._save_result_atomic(result_image, output_path)
            self.assertTrue(output_path.exists())


if __name__ == "__main__":
    unittest.main()
