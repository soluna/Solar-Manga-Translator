from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
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

    def load_patched_text_mask_utils(self):
        sys.path.insert(0, str(BACKEND_DIR / "manga-image-translator"))
        spec = importlib.util.spec_from_file_location(
            "manga_translator.mask_refinement.patched_text_mask_utils_test",
            BACKEND_DIR / "patched_text_mask_utils.py",
        )
        module = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        spec.loader.exec_module(module)
        return module

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
            BACKEND_DIR / "patched_inpainting_init.py",
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

    def test_engine_import_does_not_load_onnxruntime(self) -> None:
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

import manga_translator.manga_translator

print(json.dumps({
    "onnxruntime": "onnxruntime" in sys.modules,
    "booru_tagger": "manga_translator.inpainting.booru_tagger" in sys.modules,
    "sd_inpainter": "manga_translator.inpainting.inpainting_sd" in sys.modules,
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
                "booru_tagger": False,
                "sd_inpainter": False,
            },
        )

    def test_cli_args_import_does_not_trigger_runtime_cycle_or_onnxruntime(self) -> None:
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

from manga_translator.args import parser

print(json.dumps({
    "parser": parser.prog,
    "onnxruntime": "onnxruntime" in sys.modules,
    "booru_tagger": "manga_translator.inpainting.booru_tagger" in sys.modules,
    "sd_inpainter": "manga_translator.inpainting.inpainting_sd" in sys.modules,
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
                "parser": "manga_translator",
                "onnxruntime": False,
                "booru_tagger": False,
                "sd_inpainter": False,
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

    def test_page_image_response_path_generates_size_limited_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_path = root / "large-source.png"
            Image.new("RGB", (1200, 800), (12, 34, 56)).save(source_path)

            preview_path = engine.get_page_image_response_path(
                source_path,
                "project-a",
                "page-1.png",
                "source",
                320,
            )

            self.assertNotEqual(source_path, preview_path)
            self.assertTrue(preview_path.exists())
            with Image.open(preview_path) as preview_image:
                self.assertLessEqual(max(preview_image.size), 320)
                self.assertEqual(preview_image.size, (320, 213))

            cached_preview_path = engine.get_page_image_response_path(
                source_path,
                "project-a",
                "page-1.png",
                "source",
                320,
            )
            self.assertEqual(preview_path, cached_preview_path)

    def test_page_image_response_path_keeps_original_when_preview_is_unneeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_path = root / "small-source.png"
            Image.new("RGB", (160, 120), (255, 255, 255)).save(source_path)

            self.assertEqual(
                source_path,
                engine.get_page_image_response_path(source_path, "project-a", "page-1.png", "source"),
            )
            self.assertEqual(
                source_path,
                engine.get_page_image_response_path(source_path, "project-a", "page-1.png", "source", 0),
            )
            self.assertEqual(
                source_path,
                engine.get_page_image_response_path(source_path, "project-a", "page-1.png", "source", 480),
            )

    def test_text_mask_completion_catches_symbol_stroke_fragments(self) -> None:
        mask_utils = self.load_patched_text_mask_utils()

        class DummyAabb:
            xywh = (10, 10, 28, 28)

        class DummyTextLine:
            aabb = DummyAabb()
            font_size = 20
            area = 28 * 28

        image = np.full((48, 48, 3), 255, dtype=np.uint8)
        heart_points = np.array(
            [[13, 21], [16, 15], [22, 18], [28, 15], [34, 21], [24, 34], [13, 21]],
            dtype=np.int32,
        )
        cv2.polylines(image, [heart_points], False, (0, 0, 0), 2, lineType=cv2.LINE_8)
        cv2.circle(image, (44, 4), 2, (0, 0, 0), -1)

        partial_mask = np.zeros(image.shape[:2], dtype=np.uint8)
        cv2.polylines(partial_mask, [heart_points], False, 255, 2, lineType=cv2.LINE_8)
        partial_mask[:, :18] = 0

        enhanced, added = mask_utils._complete_ink_component_residuals(
            image,
            partial_mask,
            [DummyTextLine()],
        )

        self.assertGreater(int(added[21, 14]), 0)
        self.assertGreater(int(enhanced[21, 14]), 0)
        self.assertEqual(int(enhanced[4, 44]), 0)

    def test_single_page_rerender_skips_archive_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (4, 4), (255, 255, 255)).save(source_dir / "page-1.png")
            Image.new("RGB", (4, 4), (240, 240, 240)).save(source_dir / "page-2.png")
            existing_archive = root / "existing.zip"
            existing_archive.write_bytes(b"existing")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "rerender_cache_dir": str(root / "cache"),
                "source_images": [
                    {"name": "page-1.png", "stored_name": "page-1.png"},
                    {"name": "page-2.png", "stored_name": "page-2.png"},
                ],
                "translated_output_map": {"page-2.png": "page-2.png"},
                "download_path": str(existing_archive),
                "workflow_stage": "translated",
                "last_config": {"rerender_output_format": "png"},
            }
            events: list[dict[str, object]] = []
            persisted: dict[str, object] = {}

            async def fake_render_cached_page(*_args, **kwargs) -> None:
                output_path = kwargs.get("output_path") if "output_path" in kwargs else _args[1]
                Image.new("RGB", (4, 4), (12, 34, 56)).save(output_path)

            async def collect_event(event: dict[str, object]) -> None:
                events.append(event)

            def fail_archive(*_args, **_kwargs) -> str:
                raise AssertionError("single-page rerender should not rebuild the archive synchronously")

            def fake_persist_project_state(_project_id, _session, **kwargs) -> None:
                persisted.update(kwargs)

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._ensure_editable_page_cache = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
            engine._render_cached_page = fake_render_cached_page  # type: ignore[method-assign]
            engine.build_session_archive = fail_archive  # type: ignore[method-assign]
            engine.persist_project_state = fake_persist_project_state  # type: ignore[method-assign]

            result = asyncio.run(engine.rerender_session(
                session_id="project-a",
                session=session,
                raw_config={"rerender_output_format": "png"},
                progress_callback=collect_event,
                target_stored_name="page-1.png",
            ))

            self.assertEqual(result["download_url"], "/api/download/project-a")
            self.assertEqual(result["download_path"], str(existing_archive.resolve()))
            self.assertEqual(session["workflow_stage"], "translated")
            self.assertIn("page-1.png", session["translated_output_map"])
            self.assertEqual(persisted.get("page_ids"), ["page-1.png"])
            self.assertEqual(events[-1]["event"], "progress")
            self.assertEqual(events[-1]["current"], 1)
            self.assertEqual(events[-1]["total"], 1)


if __name__ == "__main__":
    unittest.main()
