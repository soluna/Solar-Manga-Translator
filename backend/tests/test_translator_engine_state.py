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

    def load_patched_rendering(self):
        vendor_root = BACKEND_DIR / "manga-image-translator" / "manga_translator"
        if not vendor_root.exists():
            self.skipTest("manga-image-translator vendor checkout is not installed")

        sys.path.insert(0, str(BACKEND_DIR / "manga-image-translator"))
        sys.path.insert(0, str(BACKEND_DIR))
        from patch_pydensecrf import patch_mask_refinement

        self.assertTrue(patch_mask_refinement())
        sys.modules.pop("manga_translator.rendering", None)
        import manga_translator.rendering as rendering

        return rendering

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

    def test_horizontal_renderer_uses_left_aligned_inner_text_box(self) -> None:
        rendering = self.load_patched_rendering()

        class DummyRegion:
            _direction = "auto"
            alignment = "center"
            direction = "h"
            horizontal = True

        region = DummyRegion()
        self.assertEqual(rendering._render_alignment_for_direction(region, "h"), "left")
        self.assertEqual(rendering._render_alignment_for_direction(region, "horizontal"), "left")
        self.assertEqual(rendering._render_alignment_for_direction(region, "hr"), "right")
        self.assertEqual(
            rendering._select_region_layout(region, 48, 8, None, 120, 12, True, None, ""),
            ("h", 48),
        )

        tall_candidate = np.zeros((200, 50, 4), dtype=np.uint8)
        fits, overflow, fill = rendering._layout_metrics_for_direction(tall_candidate, 60, 12, "h")
        self.assertTrue(fits)
        self.assertLessEqual(overflow, 1.0)
        self.assertGreater(fill, 0)

        padding = rendering._text_box_padding(32, 100, 60)
        self.assertGreater(padding, 0)
        inner_width, inner_height, inner_padding = rendering._inner_text_box_size(100, 60, 32)
        self.assertEqual(inner_padding, padding)
        self.assertEqual(inner_width, 100 - padding * 2)
        self.assertEqual(inner_height, 60 - padding * 2)

        temp_box = np.zeros((20, 40, 4), dtype=np.uint8)
        temp_box[:, :, 3] = 255
        canvas = rendering._compose_render_canvas(temp_box, 100, 60, "left", True, padding)
        ys, xs = np.where(canvas[:, :, 3] > 0)

        self.assertEqual(int(xs.min()), padding)
        self.assertGreaterEqual(int(ys.min()), padding)
        self.assertLessEqual(int(xs.max()), 100 - padding - 1)
        self.assertLessEqual(int(ys.max()), 60 - padding - 1)

    def test_page_payload_exposes_font_size_override_for_preview_parity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            page_document = {
                "page_id": "page-1.png",
                "dimensions": {"width": 100, "height": 200},
                "regions": [{
                    "region_id": "region-1",
                    "bbox": [1, 2, 30, 40],
                    "direction": "h",
                    "source_text": "original",
                    "translation": {"machine": "translated"},
                    "style": {
                        "font_size": 24,
                        "font_size_override": 24,
                        "rotation": -12,
                        "stroke_width": 0,
                        "letter_spacing": 1.25,
                        "line_spacing": 1.35,
                        "fg_color": "#123456",
                        "bg_color": [250, 251, 252],
                    },
                }],
            }

            translation_page = engine._page_document_to_translation_page(page_document, "page-1.png")
            style_page = engine._page_document_to_style_page(page_document, "page-1.png")

            self.assertEqual(translation_page["regions"][0]["font_size_override"], 24)
            self.assertEqual(style_page["regions"][0]["font_size_override"], 24)
            self.assertEqual(translation_page["regions"][0]["rotation"], -12)
            self.assertEqual(style_page["regions"][0]["rotation"], -12)
            self.assertEqual(translation_page["regions"][0]["stroke_width"], 0)
            self.assertEqual(style_page["regions"][0]["stroke_width"], 0)
            self.assertEqual(translation_page["regions"][0]["letter_spacing"], 1.25)
            self.assertEqual(style_page["regions"][0]["line_spacing"], 1.35)
            self.assertEqual(translation_page["regions"][0]["fg_color"], [18, 52, 86])
            self.assertEqual(style_page["regions"][0]["bg_color"], [250, 251, 252])

    def test_translation_layout_overrides_normalize_advanced_style(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            normalized = engine._normalize_translation_region_layout_overrides({
                "region-1": {
                    "rotation": 240,
                    "stroke_width": -1,
                    "letter_spacing": 4,
                    "line_spacing": 0.1,
                    "fg_color": "#abc",
                    "bg_color": "#123456",
                }
            })

            self.assertEqual(normalized["region-1"]["rotation"], 180)
            self.assertEqual(normalized["region-1"]["stroke_width"], 0)
            self.assertEqual(normalized["region-1"]["letter_spacing"], 2.5)
            self.assertEqual(normalized["region-1"]["line_spacing"], 0.5)
            self.assertEqual(normalized["region-1"]["fg_color"], [170, 187, 204])
            self.assertEqual(normalized["region-1"]["bg_color"], [18, 52, 86])

    def test_auto_text_background_color_falls_back_from_black_on_black(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            class Region:
                pass

            region = Region()
            region.fg_colors = np.array([8, 8, 8], dtype=np.uint8)
            region.bg_colors = np.array([0, 0, 0], dtype=np.uint8)

            engine._sanitize_auto_text_background_color(region, {})
            self.assertEqual(engine._rgb_color_payload(region.bg_colors, (0, 0, 0)), [255, 255, 255])

            region.bg_colors = np.array([0, 0, 0], dtype=np.uint8)
            engine._sanitize_auto_text_background_color(region, {"bg_color": [0, 0, 0]})
            self.assertEqual(engine._rgb_color_payload(region.bg_colors, (255, 255, 255)), [0, 0, 0])

    def test_numpy_region_colors_do_not_break_style_helpers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            class Region:
                pass

            region = Region()
            region.xyxy = [8, 8, 42, 34]
            region.min_rect = [np.array([[8, 8], [42, 8], [42, 34], [8, 34]], dtype=np.float32)]
            region.fg_colors = np.array([8, 8, 8], dtype=np.uint8)
            region.bg_colors = np.array([255, 255, 255], dtype=np.uint8)
            region.font_size = 18
            region.text = "测试"
            region.translation = "测试"

            source_rgb = np.full((48, 56, 3), 255, dtype=np.uint8)
            source_rgb[14:28, 16:32] = 0
            features = engine._extract_region_style_features(source_rgb, region, 18)
            self.assertGreaterEqual(features["fill_ratio"], 0.0)

            payload = engine._build_manual_region_payload(
                stored_name="page-1.png",
                bbox=[8, 8, 42, 34],
                source_text="测试",
                translation="test",
                target_lang="CHS",
                fg_color=region.fg_colors,
                bg_color=region.bg_colors,
            )
            self.assertEqual(payload["fg_color"], [8, 8, 8])
            self.assertEqual(payload["bg_color"], [255, 255, 255])

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

    def test_project_glossary_preview_uses_previous_translation_as_replacement_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")

            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "translated_output_map": {},
                "workflow_stage": "translated",
                "last_config": {},
                "translation_region_overrides": {},
                "translation_region_skip_overrides": {},
                "translation_region_disabled_overrides": {},
                "translation_region_layout_overrides": {},
                "style_region_overrides": {},
                "project_glossary": {
                    "entries": [{
                        "id": "term-yamada",
                        "source": "山田",
                        "translation": "Yamada",
                        "category": "人名",
                    }]
                },
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "dimensions": {"width": 16, "height": 16},
                "regions": [
                    {
                        "region_id": "r1",
                        "bbox": [0, 0, 8, 8],
                        "source_text": "山田来了",
                        "translation": {"machine": "Yamada来了", "resolved": "Yamada来了"},
                    },
                    {
                        "region_id": "r2",
                        "bbox": [8, 0, 8, 8],
                        "source_text": "山田也在",
                        "translation": {"machine": "山田先生也在", "resolved": "山田先生也在"},
                    },
                    {
                        "region_id": "r3",
                        "bbox": [0, 8, 8, 8],
                        "source_text": "普通对白",
                        "translation": {"machine": "Yamada", "resolved": "Yamada"},
                    },
                ],
            })

            preview = engine.preview_project_glossary_application(project_id, session, [{
                "id": "term-yamada",
                "source": "山田",
                "translation": "山田先生",
                "category": "人名",
            }])

            self.assertEqual(preview["change_count"], 1)
            self.assertEqual(preview["changes"][0]["region_id"], "r1")
            self.assertEqual(preview["changes"][0]["before"], "Yamada来了")
            self.assertEqual(preview["changes"][0]["after"], "山田先生来了")

    def test_project_glossary_apply_sets_overrides_and_rerenders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")

            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "translated_output_map": {},
                "download_path": "",
                "workflow_stage": "translated",
                "last_config": {"translator": "custom_openai", "target_lang": "CHS"},
                "translation_region_overrides": {},
                "translation_region_skip_overrides": {},
                "translation_region_disabled_overrides": {},
                "translation_region_layout_overrides": {},
                "style_region_overrides": {},
                "project_glossary": {
                    "entries": [{
                        "id": "term-yamada",
                        "source": "山田",
                        "translation": "Yamada",
                        "category": "人名",
                    }]
                },
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "dimensions": {"width": 16, "height": 16},
                "regions": [{
                    "region_id": "r1",
                    "bbox": [0, 0, 8, 8],
                    "source_text": "山田来了",
                    "translation": {"machine": "Yamada来了", "resolved": "Yamada来了"},
                }],
            })
            rerender_calls: list[dict[str, object]] = []

            async def fake_rerender_session(**kwargs):
                rerender_calls.append(kwargs)
                Image.new("RGB", (16, 16), (240, 240, 240)).save(output_dir / "page-1.png")
                session["translated_output_map"] = {"page-1.png": "page-1.png"}
                session["download_path"] = str(root / "translated.zip")
                return {
                    "download_url": f"/api/download/{project_id}",
                    "download_path": session["download_path"],
                    "translated_dir": str(output_dir),
                    "workflow_stage": "translated",
                }

            engine.rerender_session = fake_rerender_session  # type: ignore[method-assign]

            result = asyncio.run(engine.apply_project_glossary(project_id, session, [{
                "id": "term-yamada",
                "source": "山田",
                "translation": "山田先生",
                "category": "人名",
            }]))

            self.assertEqual(session["translation_region_overrides"]["r1"], "山田先生来了")
            self.assertEqual(result["change_count"], 1)
            self.assertEqual(result["glossary"]["entries"][0]["translation"], "山田先生")
            self.assertEqual(len(rerender_calls), 1)

    def test_project_glossary_save_preserves_previous_translation_for_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-2.png")

            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [
                    {"name": "page-1.png", "stored_name": "page-1.png"},
                    {"name": "page-2.png", "stored_name": "page-2.png"},
                ],
                "translated_output_map": {},
                "download_path": "",
                "workflow_stage": "translated",
                "last_config": {"translator": "custom_openai", "target_lang": "CHS"},
                "translation_region_overrides": {},
                "translation_region_skip_overrides": {},
                "translation_region_disabled_overrides": {},
                "translation_region_layout_overrides": {},
                "style_region_overrides": {},
                "project_glossary": {
                    "entries": [{
                        "id": "term-yamada",
                        "source": "山田",
                        "translation": "Yamada",
                        "category": "人名",
                    }]
                },
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "dimensions": {"width": 16, "height": 16},
                "regions": [{
                    "region_id": "r1",
                    "bbox": [0, 0, 8, 8],
                    "source_text": "山田来了",
                    "translation": {"machine": "Yamada来了", "resolved": "Yamada来了"},
                }],
            })
            engine._write_json_file(engine._project_page_document_path(project_id, "page-2.png"), {
                "page_id": "page-2.png",
                "dimensions": {"width": 16, "height": 16},
                "regions": [{
                    "region_id": "r2",
                    "bbox": [0, 0, 8, 8],
                    "source_text": "普通对白",
                    "translation": {"machine": "普通对白", "resolved": "普通对白"},
                }],
            })
            saved_glossary = engine.save_project_glossary(project_id, session, [{
                "id": "term-yamada",
                "source": "山田",
                "translation": "山田先生",
                "category": "人名",
            }])
            self.assertEqual(saved_glossary["entries"][0]["replacement"], "Yamada")

            rerender_targets: list[str | None] = []

            async def fake_rerender_session(**kwargs):
                target = kwargs.get("target_stored_name")
                rerender_targets.append(target)
                stored_name = str(target or "page-1.png")
                Image.new("RGB", (16, 16), (240, 240, 240)).save(output_dir / stored_name)
                session["translated_output_map"][stored_name] = stored_name
                return {
                    "download_url": f"/api/download/{project_id}",
                    "download_path": session.get("download_path", ""),
                    "translated_dir": str(output_dir),
                    "workflow_stage": "translated",
                }

            engine.rerender_session = fake_rerender_session  # type: ignore[method-assign]
            engine.build_session_archive = lambda *_args, **_kwargs: str(root / "translated.zip")  # type: ignore[method-assign]

            result = asyncio.run(engine.apply_project_glossary(project_id, session, saved_glossary["entries"]))

            self.assertEqual(session["translation_region_overrides"]["r1"], "山田先生来了")
            self.assertEqual(result["change_count"], 1)
            self.assertEqual(rerender_targets, ["page-1.png"])

    def test_project_glossary_apply_can_replace_untranslated_source_term(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "project_glossary": {
                    "entries": [{
                        "id": "term-ren",
                        "source": "蓮",
                        "translation": "莲",
                        "category": "人名",
                    }]
                },
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "dimensions": {"width": 16, "height": 16},
                "regions": [{
                    "region_id": "r1",
                    "bbox": [0, 0, 8, 8],
                    "source_text": "蓮来了",
                    "translation": {"machine": "蓮来了", "resolved": "蓮来了"},
                }],
            })

            preview = engine.preview_project_glossary_application(project_id, session)

            self.assertEqual(preview["change_count"], 1)
            self.assertEqual(preview["changes"][0]["after"], "莲来了")

    def test_project_glossary_lightweight_read_skips_occurrence_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            session = {
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "project_glossary": {
                    "entries": [{
                        "id": "term-yamada",
                        "source": "山田",
                        "translation": "山田",
                        "category": "人名",
                    }]
                },
            }
            engine.get_page_document = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not scan pages"))  # type: ignore[method-assign]

            glossary = engine.get_project_glossary("project-glossary", session)

            self.assertFalse(glossary["occurrences_loaded"])
            self.assertIsNone(glossary["entries"][0]["occurrence_count"])

    def test_project_glossary_extraction_uses_direct_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "project_glossary": {"entries": []},
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "dimensions": {"width": 16, "height": 16},
                "regions": [{
                    "region_id": "r1",
                    "bbox": [0, 0, 8, 8],
                    "source_text": "山田去了星见町",
                    "translation": {},
                }],
            })

            async def fail_translation_dispatcher(*_args, **_kwargs):
                raise AssertionError("glossary extraction should not use the translation dispatcher")

            async def fake_completion(config, prompt):
                self.assertEqual(config["selected_translator"], "openai-compatible")
                self.assertIn("山田去了星见町", prompt)
                return '[{"source":"山田","translation":"山田","category":"人名"}]'

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._translate_text_batch = fail_translation_dispatcher  # type: ignore[method-assign]
            engine._request_project_glossary_extraction = fake_completion  # type: ignore[method-assign]
            glossary = asyncio.run(engine.extract_project_glossary(project_id, session, {
                "translator": "custom_openai",
                "selected_translator": "openai-compatible",
                "target_lang": "CHS",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "example-model",
                "api_key": "secret",
            }))

            self.assertEqual(glossary["entries"][0]["source"], "山田")
            self.assertEqual(glossary["entries"][0]["category"], "人名")

    def test_project_glossary_parser_accepts_chinese_keys_and_wrappers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            entries = engine._parse_glossary_extraction_response(json.dumps({
                "terminology": {
                    "小夏": {
                        "译文": "小夏",
                        "类别": "角色名",
                        "说明": "角色名",
                    },
                    "星见町": {
                        "translation": "星见町",
                        "category": "地名",
                    },
                }
            }, ensure_ascii=False))

            self.assertEqual([entry["source"] for entry in entries], ["小夏", "星见町"])
            self.assertEqual(entries[0]["category"], "人名")
            self.assertEqual(entries[1]["category"], "地点")

            single_entry = engine._parse_glossary_extraction_response(
                '{"原文":"蓮","译文":"莲","类别":"角色名"}'
            )
            self.assertEqual(single_entry[0]["source"], "蓮")
            self.assertEqual(single_entry[0]["translation"], "莲")

    def test_project_glossary_extraction_retries_when_model_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-glossary"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "project_glossary": {"entries": []},
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "dimensions": {"width": 16, "height": 16},
                "regions": [{
                    "region_id": "r1",
                    "bbox": [0, 0, 8, 8],
                    "source_text": "小夏和蓮去了星见町",
                    "translation": {},
                }],
            })
            prompts: list[str] = []

            async def fake_completion(config, prompt):
                prompts.append(prompt)
                if len(prompts) == 1:
                    return "[]"
                self.assertIn("2 到 4 个字", prompt)
                return '```json\n{"items":[{"原文":"小夏","译文":"小夏","类别":"角色名"}]}\n```'

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._request_project_glossary_extraction = fake_completion  # type: ignore[method-assign]
            glossary = asyncio.run(engine.extract_project_glossary(project_id, session, {
                "translator": "openai-compatible",
                "selected_translator": "openai-compatible",
                "target_lang": "CHS",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "example-model",
                "api_key": "secret",
            }))

            self.assertEqual(len(prompts), 2)
            self.assertEqual(glossary["entries"][0]["source"], "小夏")
            self.assertEqual(glossary["entries"][0]["category"], "人名")

    def test_project_glossary_extraction_skips_translation_only_doubao_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)

            result = asyncio.run(engine._request_project_glossary_extraction({
                "translator": "custom_openai",
                "selected_translator": "doubao-ark",
                "translator_model": "doubao-seed-translation-250915",
                "api_key": "secret",
            }, "项目 OCR 原文"))

            self.assertEqual(result, "")

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

    def test_advanced_erase_composite_preserves_pixels_outside_change_mask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((80, 80, 3), 255, dtype=np.uint8)
            source[30:46, 20:62] = 0
            edited = np.full((80, 80, 3), 255, dtype=np.uint8)

            composite, mask, changed_ratio = engine._composite_advanced_erase_result(source, edited)

            self.assertGreater(int(mask[38, 36]), 0)
            self.assertGreater(changed_ratio, 0)
            self.assertLess(changed_ratio, 0.2)
            self.assertTrue(np.array_equal(composite[5, 5], source[5, 5]))
            self.assertGreater(int(composite[38, 36, 0]), 200)

    def test_advanced_erase_rejects_full_page_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((80, 80, 3), 255, dtype=np.uint8)
            edited = np.full((80, 80, 3), 120, dtype=np.uint8)

            with self.assertRaises(RuntimeError):
                engine._composite_advanced_erase_result(source, edited)

    def test_advanced_erase_traditional_backup_is_written_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            cache_dir = Path(tmp) / "cache" / "page-1"
            cache_dir.mkdir(parents=True)
            first_base = np.full((8, 8, 3), 240, dtype=np.uint8)
            second_base = np.full((8, 8, 3), 32, dtype=np.uint8)
            cv2.imwrite(str(cache_dir / "inpainted.png"), first_base)

            backup_path = engine._ensure_advanced_erase_traditional_backup(cache_dir)
            cv2.imwrite(str(cache_dir / "inpainted.png"), second_base)
            same_backup_path = engine._ensure_advanced_erase_traditional_backup(cache_dir)

            self.assertEqual(backup_path, same_backup_path)
            backup_bgr = cv2.imread(str(backup_path), cv2.IMREAD_COLOR)
            self.assertIsNotNone(backup_bgr)
            self.assertEqual(int(backup_bgr[0, 0, 0]), 240)

    def test_advanced_erase_config_is_independent_from_image_cleanup_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            config = engine.normalize_user_config({
                "image_cleanup_mode": "off",
                "image_cleanup_api_key": "old-cleanup-key",
                "advanced_erase_provider": "volcengine-ark",
                "advanced_erase_base_url": "https://ark.example.com/api/v3",
                "advanced_erase_model": "custom-seedream-model",
                "advanced_erase_api_key": "advanced-key",
                "advanced_erase_timeout_seconds": 12,
            })
            sanitized = engine._sanitize_config_for_storage(config)

            self.assertEqual(config["image_cleanup_mode"], "off")
            self.assertEqual(config["advanced_erase_provider"], "volcengine-ark")
            self.assertEqual(config["advanced_erase_base_url"], "https://ark.example.com/api/v3")
            self.assertEqual(config["advanced_erase_model"], "custom-seedream-model")
            self.assertEqual(config["advanced_erase_api_key"], "advanced-key")
            self.assertEqual(config["advanced_erase_timeout_seconds"], 30)
            self.assertEqual(sanitized["advanced_erase_api_key"], "")
            self.assertEqual(sanitized["image_cleanup_api_key"], "")


if __name__ == "__main__":
    unittest.main()
