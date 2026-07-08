from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cv2
import numpy as np
from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import engine.translator as translator_module
from engine.image_cleanup import SeedreamImageCleanupClient
from engine.translator import InvalidStorageIdentifierError, TranslatorEngine
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
        vendor_root = BACKEND_DIR / "manga-image-translator" / "manga_translator"
        if not vendor_root.exists():
            self.skipTest("manga-image-translator vendor checkout is not installed")

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

    def test_engine_command_places_general_options_after_local_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            config = engine._normalize_config({"use_gpu": True})
            (root / "source").mkdir()
            command = engine._build_command(
                root / "source",
                root / "output",
                root / "detect.json",
                config,
                prep_manual=True,
            )

            local_index = command.index("local")
            self.assertGreater(command.index("--use-gpu"), local_index)
            self.assertGreater(command.index("--model-dir"), local_index)
            self.assertEqual(
                command[command.index("--model-dir") + 1],
                str(engine.model_dir),
            )

    def test_engine_command_survives_upstream_parser(self) -> None:
        vendor_package = (
            BACKEND_DIR
            / "manga-image-translator"
            / "manga_translator"
            / "args.py"
        )
        if not vendor_package.exists():
            self.skipTest("manga-image-translator vendor checkout is not installed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            config = engine._normalize_config({"use_gpu": True})
            (root / "source").mkdir()
            command = engine._build_command(
                root / "source",
                root / "output",
                root / "detect.json",
                config,
                prep_manual=True,
            )

            engine._ensure_vendor_import_path()
            from manga_translator.args import parser, reparse

            parsed, unknown = parser.parse_known_args(command[3:])
            effective = Namespace(**{**vars(parsed), **vars(reparse(unknown))})
            self.assertTrue(effective.use_gpu)
            self.assertEqual(effective.model_dir, str(engine.model_dir))

    def test_runtime_contract_log_is_reported_as_user_facing_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            log_path = root / "detect.log"
            log_path.write_text(
                f"[RuntimeContract] device=cuda model_dir={engine.model_dir}\n",
                encoding="utf-8",
            )

            notice = engine._runtime_contract_notice(log_path)

            self.assertIn("NVIDIA CUDA", notice)
            self.assertNotIn(str(engine.model_dir), notice)

    def test_detect_profile_does_not_initialize_translation_or_inpainting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            config = engine._normalize_config(
                {
                    "translator": "gemini",
                    "api_key": "must-not-be-needed-for-detection",
                }
            )

            config_path = engine._write_config("project-a", config, profile="detect")
            payload = json.loads(config_path.read_text(encoding="utf-8"))

            self.assertEqual(payload["translator"]["translator"], "none")
            self.assertEqual(payload["inpainter"]["inpainter"], "original")
            self.assertEqual(payload["render"]["renderer"], "none")

    def test_detect_only_runtime_patch_returns_before_translation_mask_and_inpainting(self) -> None:
        runtime_path = (
            BACKEND_DIR
            / "manga-image-translator"
            / "manga_translator"
            / "manga_translator.py"
        )
        if not runtime_path.exists():
            self.skipTest("manga-image-translator vendor checkout is not installed")

        content = runtime_path.read_text(encoding="utf-8")
        early_return = content.index("if self.prep_manual:", content.index("# Apply pre-dictionary"))
        translation_stage = content.index("# -- Translation", early_return)
        mask_stage = content.index("# -- Mask refinement", translation_stage)

        self.assertLess(early_return, translation_stage)
        self.assertLess(translation_stage, mask_stage)
        preload_block = content[
            content.index("# Solar-Manga-Translator: detection")
            : content.index("# translate", content.index("# Solar-Manga-Translator: detection"))
        ]
        self.assertIn("if not self.prep_manual:", preload_block)
        self.assertIn("await prepare_inpainting", preload_block)
        self.assertIn("await prepare_translation", preload_block)
        self.assertIn("MT_DISABLE_INTERNAL_LOG_FILE", content)

    def test_engine_environment_routes_logs_to_application_log_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            env = engine._build_env(engine._normalize_config({}))

            self.assertEqual(env["MT_DISABLE_INTERNAL_LOG_FILE"], "1")

    def test_failed_detect_restores_previous_outputs_cache_and_session_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-a"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            Image.new("RGB", (16, 16), (1, 2, 3)).save(output_dir / "page-1.png")
            cache_dir = engine._prepare_rerender_cache_dir(project_id, reset=True)
            page_cache_dir = cache_dir / "page-1.png"
            page_cache_dir.mkdir()
            (page_cache_dir / "regions.json").write_text("[]", encoding="utf-8")
            Image.new("RGB", (16, 16), (4, 5, 6)).save(page_cache_dir / "inpainted.png")
            existing_archive = root / "existing.zip"
            existing_archive.write_bytes(b"existing")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "download_path": str(existing_archive),
                "translated_output_map": {"page-1.png": "page-1.png"},
                "workflow_stage": "translated",
                "rerender_cache_dir": str(cache_dir),
                "manual_regions": {},
            }
            engine.initialize_project(project_id, session, title="Existing project")
            persisted_state_path = engine._project_session_state_path(project_id)
            persisted_state_before = persisted_state_path.read_bytes()

            async def fail_command(**_kwargs):
                return 1

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._run_translation_command = fail_command  # type: ignore[method-assign]
            engine._format_failure = lambda _path: "synthetic failure"  # type: ignore[method-assign]

            async def progress(_event):
                return None

            with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
                asyncio.run(
                    engine.detect_session(
                        session_id=project_id,
                        session=session,
                        raw_config={"translator": "gemini", "api_key": "invalid"},
                        progress_callback=progress,
                    )
                )

            self.assertEqual(session["workflow_stage"], "translated")
            self.assertEqual(session["download_path"], str(existing_archive))
            self.assertEqual(session["translated_output_map"], {"page-1.png": "page-1.png"})
            self.assertEqual(
                np.asarray(Image.open(output_dir / "page-1.png"))[0, 0].tolist(),
                [1, 2, 3],
            )
            self.assertEqual(
                np.asarray(Image.open(page_cache_dir / "inpainted.png"))[0, 0].tolist(),
                [4, 5, 6],
            )
            self.assertEqual(persisted_state_path.read_bytes(), persisted_state_before)

    def test_translation_stage_builds_inpainted_base_from_detected_regions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_path = root / "page-1.png"
            page_cache = root / "cache" / "page-1.png"
            page_cache.mkdir(parents=True)
            Image.new("RGB", (48, 48), (255, 255, 255)).save(source_path)
            Image.new("RGB", (48, 48), (255, 255, 255)).save(page_cache / "inpainted.png")
            (page_cache / "meta.json").write_text(
                json.dumps({"base_kind": "source"}),
                encoding="utf-8",
            )
            region = SimpleNamespace(
                lines=[[[10, 10], [30, 10], [30, 30], [10, 30]]],
                xyxy=[10, 10, 30, 30],
                font_size=16,
                disabled_region=False,
            )
            captured: dict[str, object] = {}

            async def fake_inpaint(base_rgb, selection_mask, *, device):
                captured["mask_nonzero"] = int(np.count_nonzero(selection_mask))
                captured["device"] = device
                return np.zeros_like(base_rgb)

            engine._load_cached_regions = lambda _path: [region]  # type: ignore[method-assign]
            engine._run_local_lama_inpaint = fake_inpaint  # type: ignore[method-assign]

            asyncio.run(
                engine._ensure_translation_base_image(
                    source_path=source_path,
                    page_cache_dir=page_cache,
                    config={"use_gpu": False},
                )
            )

            self.assertGreater(captured["mask_nonzero"], 0)
            self.assertEqual(captured["device"], "cpu")
            self.assertEqual(
                json.loads((page_cache / "meta.json").read_text(encoding="utf-8"))["base_kind"],
                "inpainted",
            )
            self.assertEqual(
                np.asarray(Image.open(page_cache / "inpainted.png"))[0, 0].tolist(),
                [0, 0, 0],
            )

    def test_translation_base_cleans_white_caption_residue_after_local_lama(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_path = root / "page-1.png"
            page_cache = root / "cache" / "page-1.png"
            page_cache.mkdir(parents=True)

            source = np.full((240, 240, 3), 128, dtype=np.uint8)
            cv2.rectangle(source, (86, 50), (142, 152), (255, 255, 255), -1)
            cv2.rectangle(source, (86, 50), (142, 152), (0, 0, 0), 2)
            cv2.line(source, (114, 78), (114, 138), (0, 0, 0), 2)
            cv2.line(source, (128, 106), (128, 132), (64, 64, 64), 2)
            Image.fromarray(source).save(source_path)
            Image.fromarray(source).save(page_cache / "inpainted.png")
            (page_cache / "meta.json").write_text(
                json.dumps({"base_kind": "source"}),
                encoding="utf-8",
            )

            region = SimpleNamespace(
                lines=[[[108, 74], [120, 74], [120, 142], [108, 142]]],
                xyxy=[108, 74, 120, 142],
                font_size=14,
                disabled_region=False,
            )

            async def fake_inpaint(base_rgb, selection_mask, *, device):
                edited = base_rgb.copy()
                edited[selection_mask > 0] = [248, 248, 248]
                edited[106:133, 126:131] = [64, 64, 64]
                return edited

            engine._load_cached_regions = lambda _path: [region]  # type: ignore[method-assign]
            engine._run_local_lama_inpaint = fake_inpaint  # type: ignore[method-assign]
            engine._select_local_inpainting_device = lambda _use_gpu: "cpu"  # type: ignore[method-assign]

            asyncio.run(
                engine._ensure_translation_base_image(
                    source_path=source_path,
                    page_cache_dir=page_cache,
                    config={"use_gpu": False, "mask_cleanup_strength": "standard"},
                )
            )

            output = np.asarray(Image.open(page_cache / "inpainted.png").convert("RGB"))
            self.assertGreater(int(output[116, 128, 0]), 240)
            self.assertLess(int(output[50, 114, 0]), 32)

    def test_translation_base_upgrades_existing_white_caption_residue_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_path = root / "page-1.png"
            page_cache = root / "cache" / "page-1.png"
            page_cache.mkdir(parents=True)

            source = np.full((240, 240, 3), 128, dtype=np.uint8)
            cv2.rectangle(source, (86, 50), (142, 152), (255, 255, 255), -1)
            cv2.rectangle(source, (86, 50), (142, 152), (0, 0, 0), 2)
            cv2.line(source, (114, 78), (114, 138), (0, 0, 0), 2)
            cv2.line(source, (128, 106), (128, 132), (64, 64, 64), 2)
            Image.fromarray(source).save(source_path)

            stale_base = source.copy()
            stale_base[108:140, 108:121] = [248, 248, 248]
            stale_base[106:133, 126:131] = [64, 64, 64]
            Image.fromarray(stale_base).save(page_cache / "inpainted.png")
            (page_cache / "meta.json").write_text(
                json.dumps({"base_kind": "inpainted"}),
                encoding="utf-8",
            )

            region = SimpleNamespace(
                lines=[[[108, 74], [120, 74], [120, 142], [108, 142]]],
                xyxy=[108, 74, 120, 142],
                font_size=14,
                disabled_region=False,
            )

            engine._load_cached_regions = lambda _path: [region]  # type: ignore[method-assign]

            async def fail_inpaint(*_args, **_kwargs):
                raise AssertionError("existing inpainted caches should not rerun LaMa")

            engine._run_local_lama_inpaint = fail_inpaint  # type: ignore[method-assign]

            asyncio.run(
                engine._ensure_translation_base_image(
                    source_path=source_path,
                    page_cache_dir=page_cache,
                    config={"use_gpu": False, "mask_cleanup_strength": "standard"},
                )
            )

            output = np.asarray(Image.open(page_cache / "inpainted.png").convert("RGB"))
            meta = json.loads((page_cache / "meta.json").read_text(encoding="utf-8"))
            self.assertGreater(int(output[116, 128, 0]), 240)
            self.assertLess(int(output[50, 114, 0]), 32)
            self.assertEqual(meta["white_container_cleanup_version"], 1)

    def test_cpu_inpainting_device_does_not_import_pytorch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            with mock.patch.dict(sys.modules, {"torch": None}):
                self.assertEqual(
                    engine._select_local_inpainting_device(False),
                    "cpu",
                )

    def test_inference_device_uses_mps_when_cuda_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            fake_torch = SimpleNamespace(
                cuda=SimpleNamespace(is_available=lambda: False),
                backends=SimpleNamespace(
                    mps=SimpleNamespace(is_available=lambda: True),
                ),
            )
            with mock.patch.dict(sys.modules, {"torch": fake_torch}):
                self.assertEqual(engine._select_inference_device(True), "mps")

    def test_successful_detect_atomically_commits_staged_outputs_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-a"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (16, 16), (255, 255, 255)).save(source_dir / "page-1.png")
            Image.new("RGB", (16, 16), (200, 0, 0)).save(output_dir / "page-1.png")
            live_cache = engine._prepare_rerender_cache_dir(project_id, reset=True)
            old_cache_page = live_cache / "page-1.png"
            old_cache_page.mkdir()
            (old_cache_page / "regions.json").write_text("[]", encoding="utf-8")
            Image.new("RGB", (16, 16), (200, 0, 0)).save(old_cache_page / "inpainted.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "download_path": None,
                "translated_output_map": {"page-1.png": "page-1.png"},
                "workflow_stage": "translated",
                "rerender_cache_dir": str(live_cache),
                "manual_regions": {},
            }
            engine.initialize_project(project_id, session, title="Existing project")

            async def fake_command(**kwargs):
                staged_session = kwargs["session"]
                staged_output = Path(staged_session["translated_dir"])
                staged_cache_page = Path(staged_session["rerender_cache_dir"]) / "page-1.png"
                staged_output.mkdir(parents=True, exist_ok=True)
                staged_cache_page.mkdir(parents=True, exist_ok=True)
                Image.new("RGB", (16, 16), (0, 200, 0)).save(staged_output / "page-1.png")
                Image.new("RGB", (16, 16), (0, 200, 0)).save(staged_cache_page / "inpainted.png")
                (staged_cache_page / "regions.json").write_text("[]", encoding="utf-8")
                staged_session["translated_output_map"] = {"page-1.png": "page-1.png"}
                return 0

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._run_translation_command = fake_command  # type: ignore[method-assign]

            async def progress(_event):
                return None

            result = asyncio.run(
                engine.detect_session(
                    session_id=project_id,
                    session=session,
                    raw_config={"translator": "none"},
                    progress_callback=progress,
                )
            )

            self.assertEqual(result["translated_dir"], str(output_dir.resolve()))
            self.assertEqual(session["translated_dir"], str(output_dir))
            self.assertEqual(session["rerender_cache_dir"], str(live_cache))
            self.assertEqual(
                np.asarray(Image.open(output_dir / "page-1.png"))[0, 0].tolist(),
                [0, 200, 0],
            )
            self.assertEqual(
                np.asarray(Image.open(live_cache / "page-1.png" / "inpainted.png"))[0, 0].tolist(),
                [0, 200, 0],
            )

    def test_project_storage_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            sentinel = engine.paths.app_data_dir / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")

            for invalid_project_id in ("", ".", "..", "../outside", "nested/project", "nested\\project", "\x00"):
                with self.subTest(project_id=repr(invalid_project_id)):
                    with self.assertRaises(InvalidStorageIdentifierError):
                        engine.delete_project(invalid_project_id)

            self.assertTrue(sentinel.exists())
            self.assertEqual(
                engine._project_dir("legacy.project_1-test"),
                engine.projects_root.resolve() / "legacy.project_1-test",
            )

    def test_page_storage_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            for invalid_page_id in ("", ".", "..", "../page.png", "nested/page.png", "nested\\page.png", "\x00"):
                with self.subTest(page_id=repr(invalid_page_id)):
                    with self.assertRaises(InvalidStorageIdentifierError):
                        engine._project_page_document_path("project-a", invalid_page_id)

            self.assertEqual(
                engine._project_page_document_path("project-a", "0001.png"),
                engine.projects_root.resolve() / "project-a" / "pages" / "0001.png" / "page_document.json",
            )

    def test_stroke_strength_accepts_values_above_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            self.assertEqual(engine._normalize_stroke_strength(3.25), 3.25)
            self.assertEqual(engine._normalize_stroke_strength(99), 5.0)

    def test_duplicate_region_copies_style_and_offsets_bbox(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "duplicate-project"
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            Image.new("RGB", (400, 600), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {"target_lang": "CHS"},
                "manual_regions": {},
            }
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "dimensions": {"width": 400, "height": 600},
                "regions": [{
                    "region_id": "source-region",
                    "bbox": [40, 50, 180, 240],
                    "direction": "v",
                    "source_text": "原文",
                    "translation": {"machine": "译文", "edited": "新译文", "resolved": "新译文"},
                    "style": {
                        "font_style": "handwritten",
                        "font_key_override": "project:test.ttf",
                        "font_size": 36,
                        "letter_spacing": 1.2,
                        "line_spacing": 1.3,
                        "alignment": "left",
                        "fg_color": [12, 34, 56],
                        "bg_color": [240, 241, 242],
                        "stroke_width": 2.5,
                        "rotation": 8,
                    },
                    "flags": {
                        "disabled": True,
                        "keep_original": True,
                        "preserve_background": True,
                    },
                }],
            })

            duplicated = engine.duplicate_region(
                project_id=project_id,
                session=session,
                stored_name="page-1.png",
                region_id="source-region",
                raw_config={"target_lang": "CHS"},
            )

            duplicated_id = duplicated["id"]
            self.assertNotEqual(duplicated["bbox"], [40, 50, 180, 240])
            self.assertEqual(duplicated["font_size"], 36)
            self.assertEqual(duplicated["stroke_width"], 2.5)
            self.assertEqual(session["translation_region_overrides"][duplicated_id], "新译文")
            self.assertEqual(session["translation_region_layout_overrides"][duplicated_id]["font_key"], "project:test.ttf")
            self.assertEqual(session["style_region_overrides"][duplicated_id], "handwritten")
            self.assertTrue(session["translation_region_disabled_overrides"][duplicated_id])
            self.assertTrue(session["translation_region_skip_overrides"][duplicated_id])
            original_region = SimpleNamespace(
                xyxy=[40, 50, 180, 240],
                translation="新译文",
                text="原文",
                font_size=36,
                manual_region=False,
                allow_overlap=False,
            )
            duplicated_region = SimpleNamespace(
                xyxy=duplicated["bbox"],
                translation="新译文",
                text="原文",
                font_size=36,
                manual_region=True,
                allow_overlap=True,
            )
            self.assertEqual(len(engine._dedupe_overlapping_regions([original_region, duplicated_region])), 2)

    def test_export_archives_use_project_result_and_blank_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            Image.new("RGB", (20, 20), (255, 255, 255)).save(source_dir / "001.jpg")
            Image.new("RGB", (20, 20), (245, 245, 245)).save(source_dir / "002.png")
            Image.new("RGB", (20, 20), (0, 0, 0)).save(translated_dir / "001.png")
            Image.new("RGB", (20, 20), (10, 10, 10)).save(translated_dir / "002.png")
            session = {
                "project_title": "项目:测试",
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [
                    {"name": "001.jpg", "stored_name": "001.jpg"},
                    {"name": "002.png", "stored_name": "002.png"},
                ],
                "translated_output_map": {
                    "001.jpg": "001.png",
                    "002.png": "002.png",
                },
                "last_config": {"rerender_output_format": "png"},
            }

            result_archive = engine.build_session_archive("project-a", session)
            blank_archive = engine.build_blank_session_archive("project-a", session)

            with zipfile.ZipFile(result_archive) as archive:
                self.assertEqual(archive.namelist(), [
                    "项目_测试_result_0001.png",
                    "项目_测试_result_0002.png",
                ])
                self.assertTrue(all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist()))
            with zipfile.ZipFile(blank_archive) as archive:
                self.assertEqual(archive.namelist(), [
                    "项目_测试_blank_0001.png",
                    "项目_测试_blank_0002.png",
                ])
                self.assertTrue(all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist()))
            self.assertEqual(engine.get_export_archive_filename("project-a", session, "result"), "项目_测试_result.zip")
            self.assertEqual(engine.get_export_archive_filename("project-a", session, "blank"), "项目_测试_blank.zip")

    def test_project_summary_and_payload_include_persisted_region_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            Image.new("RGB", (20, 20), (255, 255, 255)).save(source_dir / "001.png")
            Image.new("RGB", (20, 20), (245, 245, 245)).save(source_dir / "002.png")
            session = {
                "project_title": "框数项目",
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [
                    {"name": "001.png", "stored_name": "001.png"},
                    {"name": "002.png", "stored_name": "002.png"},
                ],
                "translated_output_map": {},
                "workflow_stage": "detected",
                "last_config": {"rerender_output_format": "png"},
            }
            engine._write_json_file(engine._project_page_document_path("project-a", "001.png"), {
                "regions": [{"id": "a"}, {"id": "b"}],
            })
            engine._write_json_file(engine._project_page_document_path("project-a", "002.png"), {
                "regions": [{"id": "c"}],
            })

            summary = engine._build_project_summary("project-a", session)
            payload = engine.build_client_session_payload("project-a", session)

            self.assertEqual(summary["region_count"], 3)
            self.assertEqual(payload["project"]["region_count"], 3)
            self.assertEqual([image["region_count"] for image in payload["images"]], [2, 1])

    def test_build_session_archive_rejects_missing_translated_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            Image.new("RGB", (20, 20), (255, 255, 255)).save(source_dir / "001.png")
            Image.new("RGB", (20, 20), (245, 245, 245)).save(source_dir / "002.png")
            Image.new("RGB", (20, 20), (0, 0, 0)).save(translated_dir / "001.png")
            session = {
                "project_title": "缺页项目",
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [
                    {"name": "001.png", "stored_name": "001.png"},
                    {"name": "002.png", "stored_name": "002.png"},
                ],
                "translated_output_map": {"001.png": "001.png"},
                "last_config": {"rerender_output_format": "png"},
            }

            with self.assertRaisesRegex(RuntimeError, "缺少翻译结果"):
                engine.build_session_archive("project-a", session)

            self.assertFalse((translated_dir / "002.png").exists())

    def test_project_glossary_extraction_uses_limited_context_and_longer_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "project_glossary": {"entries": []},
            }
            very_long_text = "山田 小夏 " * 5000
            engine._project_text_context_for_glossary = lambda *_args, **_kwargs: very_long_text  # type: ignore[method-assign]
            captured: dict[str, object] = {}

            def fake_completion(**kwargs) -> str:
                captured.update(kwargs)
                return '[{"source":"山田","translation":"山田","category":"人名"}]'

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._request_chat_completions_text_sync = fake_completion  # type: ignore[method-assign]

            glossary = asyncio.run(engine.extract_project_glossary("project-a", session, {
                "translator": "custom_openai",
                "selected_translator": "openai-compatible",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "model",
                "api_key": "key",
                "target_lang": "CHS",
            }, force=True))

            self.assertEqual(glossary["entries"][0]["source"], "山田")
            self.assertLessEqual(len(str(captured["user_prompt"])), engine.PROJECT_GLOSSARY_PROMPT_CHAR_LIMIT)
            self.assertEqual(captured["timeout_seconds"], engine.PROJECT_GLOSSARY_REQUEST_TIMEOUT_SECONDS)

    def test_brush_edit_operations_paint_restore_and_erase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            base = np.full((100, 100, 3), 240, dtype=np.uint8)
            source = np.full((100, 100, 3), (20, 40, 180), dtype=np.uint8)
            operations = engine._normalize_brush_edit_operations(
                [
                    {
                        "mode": "paint",
                        "color": [255, 0, 0],
                        "size": 18,
                        "points": [[0.2, 0.2]],
                    },
                    {
                        "mode": "restore",
                        "size": 18,
                        "points": [[0.5, 0.5]],
                    },
                    {
                        "mode": "paint",
                        "color": [0, 0, 0],
                        "size": 18,
                        "points": [[0.8, 0.8]],
                    },
                    {
                        "mode": "erase",
                        "size": 18,
                        "points": [[0.8, 0.8]],
                    },
                ],
                base.shape,
            )

            edited = engine._apply_brush_edit_operations(base, source, operations)

            np.testing.assert_array_equal(edited[20, 20], np.array([255, 0, 0], dtype=np.uint8))
            np.testing.assert_array_equal(edited[50, 50], source[50, 50])
            np.testing.assert_array_equal(edited[80, 80], base[80, 80])

    def test_delete_project_removes_project_storage_and_preview_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            project_id = "project-delete"
            paths_to_remove = [
                engine._project_dir(project_id),
                engine._project_output_dir(project_id),
                engine._rerender_cache_dir(project_id),
                engine._mask_debug_dir(project_id),
                engine._style_rerender_debug_dir(project_id),
                engine._image_preview_project_cache_dir(project_id),
            ]
            for path in paths_to_remove:
                path.mkdir(parents=True, exist_ok=True)
                (path / "marker.txt").write_text("x", encoding="utf-8")
            (engine.temp_dir / f"{project_id}_detect.log").write_text("log", encoding="utf-8")
            engine._write_project_index([
                {"project_id": project_id, "title": "delete me"},
                {"project_id": "keep-project", "title": "keep me"},
            ])

            engine.delete_project(project_id)

            for path in paths_to_remove:
                self.assertFalse(path.exists(), str(path))
            self.assertFalse((engine.temp_dir / f"{project_id}_detect.log").exists())
            remaining = engine._read_json_file(engine.project_index_path, [])
            self.assertEqual([item["project_id"] for item in remaining], ["keep-project"])

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

    def test_translation_override_preserves_single_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            session = {"translation_region_overrides": {}}

            engine._set_region_translation_override_value(session, "region-1", " ")
            self.assertEqual(session["translation_region_overrides"]["region-1"], " ")

            normalized = engine._normalize_translation_region_overrides({"region-1": " "})
            self.assertEqual(normalized, {"region-1": " "})

            engine._set_region_translation_override_value(session, "region-1", "")
            self.assertNotIn("region-1", session["translation_region_overrides"])

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

    def test_restore_recovers_rerender_variant_outputs_and_translated_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            project_id = "rerender-restore"
            source_dir = engine._project_source_dir(project_id)
            translated_dir = engine._project_translated_dir(project_id)
            source_dir.mkdir(parents=True)
            translated_dir.mkdir(parents=True)
            Image.new("RGB", (8, 8), (255, 255, 255)).save(source_dir / "page-1.png")
            Image.new("RGB", (8, 8), (240, 240, 240)).save(source_dir / "page-2.png")
            Image.new("RGB", (8, 8), (20, 20, 20)).save(translated_dir / "page-1.png")
            Image.new("RGB", (8, 8), (30, 30, 30)).save(translated_dir / "page-1__rerender-2.png")
            Image.new("RGB", (8, 8), (40, 40, 40)).save(translated_dir / "page-2__rerender-2.png")
            os.utime(translated_dir / "page-1.png", (1, 1))
            os.utime(translated_dir / "page-1__rerender-2.png", (2, 2))
            os.utime(translated_dir / "page-2__rerender-2.png", (2, 2))
            state = {
                "project_id": project_id,
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [
                    {"name": "page-1.png", "stored_name": "page-1.png"},
                    {"name": "page-2.png", "stored_name": "page-2.png"},
                ],
                "translated_output_map": {},
                "workflow_stage": "detected",
                "last_config": {"rerender_output_format": "png"},
            }
            engine._write_json_file(engine._project_session_state_path(project_id), state)
            engine._write_json_file(engine._project_manifest_path(project_id), {"project_id": project_id})

            session = engine.restore_project_session(project_id)
            payload = engine.build_client_session_payload(project_id, session)

            self.assertEqual(session["workflow_stage"], "translated")
            self.assertEqual(session["translated_output_map"]["page-1.png"], "page-1__rerender-2.png")
            self.assertEqual(session["translated_output_map"]["page-2.png"], "page-2__rerender-2.png")
            self.assertEqual(payload["workflow_stage"], "translated")
            self.assertEqual(len(payload["translated_images"]), 2)

    def test_restore_recovers_detected_stage_from_persisted_page_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            project_id = "detected-restore"
            source_dir = engine._project_source_dir(project_id)
            translated_dir = engine._project_translated_dir(project_id)
            source_dir.mkdir(parents=True)
            translated_dir.mkdir(parents=True)
            Image.new("RGB", (8, 8), (255, 255, 255)).save(source_dir / "page-1.png")
            state = {
                "project_id": project_id,
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "translated_output_map": {},
                "workflow_stage": "idle",
                "last_config": {},
            }
            engine._write_json_file(engine._project_session_state_path(project_id), state)
            engine._write_json_file(engine._project_manifest_path(project_id), {"project_id": project_id})
            engine._write_json_file(engine._project_page_document_path(project_id, "page-1.png"), {
                "page_id": "page-1.png",
                "regions": [{
                    "region_id": "region-1",
                    "bbox": [1, 1, 4, 4],
                    "source_text": "こんにちは",
                    "translation": {"machine": "", "resolved": "こんにちは"},
                }],
            })

            session = engine.restore_project_session(project_id)

            self.assertEqual(session["workflow_stage"], "detected")

    def test_editable_cache_repairs_unreadable_base_from_traditional_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            project_id = "cache-repair"
            source_dir = engine._project_source_dir(project_id)
            source_dir.mkdir(parents=True)
            source_path = source_dir / "page-1.png"
            Image.new("RGB", (8, 8), (255, 255, 255)).save(source_path)
            session = {
                "source_dir": str(source_dir),
                "rerender_cache_dir": str(Path(tmp) / "rerender-cache"),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
            }
            cache_dir = engine._session_page_cache_dir(session, project_id, "page-1.png")
            cache_dir.mkdir(parents=True)
            (cache_dir / "regions.json").write_text("[]", encoding="utf-8")
            (cache_dir / "inpainted.png").write_bytes(b"not-a-png")
            backup_path = engine._advanced_erase_traditional_backup_path(cache_dir)
            backup_path.parent.mkdir(parents=True)
            backup_bgr = np.zeros((8, 8, 3), dtype=np.uint8)
            backup_bgr[:, :] = [12, 34, 56]
            cv2.imwrite(str(backup_path), backup_bgr)

            repaired = engine._ensure_editable_page_cache(
                session_id=project_id,
                session=session,
                stored_name="page-1.png",
                config={},
                source_path=source_path,
            )
            repaired_bgr = cv2.imread(str(cache_dir / "inpainted.png"), cv2.IMREAD_COLOR)

            self.assertTrue(repaired)
            self.assertIsNotNone(repaired_bgr)
            self.assertEqual(repaired_bgr[0, 0].tolist(), [12, 34, 56])
            self.assertTrue(any(path.name.startswith("inpainted.corrupt-") for path in cache_dir.iterdir()))

    def test_editable_cache_allows_empty_region_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            project_id = "empty-region-cache"
            source_dir = engine._project_source_dir(project_id)
            source_dir.mkdir(parents=True)
            source_path = source_dir / "0200.jpg"
            Image.new("RGB", (8, 8), (255, 255, 255)).save(source_path)
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(engine._project_translated_dir(project_id)),
                "rerender_cache_dir": str(Path(tmp) / "rerender-cache"),
                "source_images": [{"name": "0200.jpg", "stored_name": "0200.jpg"}],
                "manual_regions": {},
                "last_config": {},
            }

            restored = engine._ensure_editable_page_cache(
                session_id=project_id,
                session=session,
                stored_name="0200.jpg",
                config={},
                source_path=source_path,
            )
            cache_dir = engine._session_page_cache_dir(session, project_id, "0200.jpg")
            regions = json.loads((cache_dir / "regions.json").read_text(encoding="utf-8"))

            self.assertTrue(restored)
            self.assertEqual(regions, [])
            self.assertTrue((cache_dir / "inpainted.png").exists())

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

    def test_settings_validation_treats_empty_preview_as_successful_connection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            def fake_validation_request(**_kwargs):
                return ""

            engine._request_chat_completions_validation_sync = fake_validation_request  # type: ignore[method-assign]

            result = asyncio.run(engine.validate_user_config({
                "translator": "openai-compatible",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "example-model",
                "api_key": "secret",
            }))

            self.assertTrue(result.get("ok"))
            self.assertEqual(result.get("message"), "连接成功")
            self.assertEqual(result.get("preview"), "")

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

    def test_openai_compatible_validation_request_uses_app_user_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            captured = {}

            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self):
                    return json.dumps({
                        "choices": [{
                            "message": {
                                "content": "测试"
                            }
                        }]
                    }).encode("utf-8")

            def fake_urlopen(request, timeout=0):
                captured["url"] = request.full_url
                captured["timeout"] = timeout
                captured["headers"] = dict(request.header_items())
                return FakeResponse()

            with mock.patch.object(translator_module.urllib_request, "urlopen", fake_urlopen):
                result = engine._request_chat_completions_validation_sync(
                    provider_label="OpenAI Compatible",
                    base_url="https://api.example.com/v1/chat/completions",
                    model="example-model",
                    api_key="secret",
                )

            self.assertEqual(result, "测试")
            self.assertEqual(captured["url"], "https://api.example.com/v1/chat/completions")
            self.assertEqual(captured["timeout"], 30)
            self.assertIn("Solar-Manga-Translator", captured["headers"].get("User-agent", ""))
            self.assertEqual(captured["headers"].get("Accept"), "application/json")

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
                    "flags": {
                        "preserve_background": True,
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
            self.assertTrue(translation_page["regions"][0]["preserve_background"])
            self.assertTrue(style_page["regions"][0]["preserve_background"])

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
                    "preserve_background": True,
                }
            })

            self.assertEqual(normalized["region-1"]["rotation"], 180)
            self.assertEqual(normalized["region-1"]["stroke_width"], 0)
            self.assertEqual(normalized["region-1"]["letter_spacing"], 2.5)
            self.assertEqual(normalized["region-1"]["line_spacing"], 0.5)
            self.assertEqual(normalized["region-1"]["fg_color"], [170, 187, 204])
            self.assertEqual(normalized["region-1"]["bg_color"], [18, 52, 86])
            self.assertTrue(normalized["region-1"]["preserve_background"])

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

    def test_manual_region_is_created_before_ocr_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            source_dir.mkdir()
            Image.new("RGB", (240, 320), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "manual_regions": {},
            }

            async def fail_ocr(*_args, **_kwargs):
                raise RuntimeError("OCR runtime unavailable")

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._ocr_manual_region = fail_ocr  # type: ignore[method-assign]

            region = asyncio.run(engine.create_manual_region(
                session_id="manual-project",
                session=session,
                raw_config={"translator": "none", "target_lang": "CHS", "use_gpu": False},
                stored_name="page-1.png",
                bbox=[20, 30, 140, 190],
            ))

            self.assertEqual(region["bbox"], [20, 30, 140, 190])
            self.assertEqual(region["source_text"], "")
            self.assertEqual(
                session["manual_regions"]["page-1.png"][0]["id"],
                region["id"],
            )

    def test_manual_region_survives_ocr_failure_and_can_be_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            source_dir.mkdir()
            Image.new("RGB", (240, 320), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "manual_regions": {},
            }
            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            region = asyncio.run(engine.create_manual_region(
                session_id="manual-project",
                session=session,
                raw_config={"translator": "none", "target_lang": "CHS", "use_gpu": False},
                stored_name="page-1.png",
                bbox=[20, 30, 140, 190],
            ))

            async def fail_ocr(*_args, **_kwargs):
                raise RuntimeError("OCR runtime unavailable")

            engine._ocr_manual_region = fail_ocr  # type: ignore[method-assign]
            retried = asyncio.run(engine.recognize_manual_region(
                session_id="manual-project",
                session=session,
                raw_config={"translator": "none", "target_lang": "CHS", "use_gpu": False},
                stored_name="page-1.png",
                region_id=region["id"],
            ))

            self.assertEqual(retried["id"], region["id"])
            self.assertEqual(retried["recognition_status"], "failed")
            self.assertIn("OCR runtime unavailable", retried["recognition_error"])
            self.assertEqual(
                session["manual_regions"]["page-1.png"][0]["id"],
                region["id"],
            )

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

    def test_page_image_response_path_uses_cache_before_resizing_again(self) -> None:
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

            with mock.patch.object(Image.Image, "resize", side_effect=AssertionError("cache miss")):
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

    def test_inspection_can_load_one_persisted_page_without_rebuilding_all_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-inspection"
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [
                    {"name": "page 1", "stored_name": "page-1.png"},
                    {"name": "page 2", "stored_name": "page-2.png"},
                ],
                "translated_output_map": {},
                "last_config": {},
                "workflow_stage": "translated",
            }

            for page_id in ("page-1.png", "page-2.png"):
                Image.new("RGB", (120, 160), (255, 255, 255)).save(source_dir / page_id)
                engine._write_json_file(
                    engine._project_page_document_path(project_id, page_id),
                    {
                        "version": engine.PAGE_DOCUMENT_VERSION,
                        "page_id": page_id,
                        "dimensions": {"width": 120, "height": 160},
                        "regions": [],
                    },
                )

            with mock.patch.object(
                engine,
                "_build_page_document",
                side_effect=AssertionError("persisted page should be reused"),
            ):
                review_payload = asyncio.run(
                    engine.inspect_translation_regions(
                        project_id,
                        session,
                        {},
                        target_stored_name="page-2.png",
                    )
                )
                style_payload = asyncio.run(
                    engine.inspect_style_regions(
                        project_id,
                        session,
                        {},
                        target_stored_name="page-2.png",
                    )
                )

            self.assertEqual([page["stored_name"] for page in review_payload["pages"]], ["page-2.png"])
            self.assertEqual([page["stored_name"] for page in style_payload["pages"]], ["page-2.png"])

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

    def test_project_glossary_auto_extraction_marks_empty_result_complete(self) -> None:
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
                "regions": [{
                    "region_id": "r1",
                    "bbox": [0, 0, 8, 8],
                    "source_text": "山田和小夏在这里",
                    "translation": {"machine": "", "resolved": ""},
                }],
            })
            calls = 0

            async def fake_completion(_config, _prompt):
                nonlocal calls
                calls += 1
                return "[]"

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._request_project_glossary_extraction = fake_completion  # type: ignore[method-assign]
            config = {
                "translator": "openai-compatible",
                "selected_translator": "openai-compatible",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "model",
                "api_key": "secret",
                "target_lang": "CHS",
            }

            glossary = asyncio.run(engine.extract_project_glossary(project_id, session, config))
            glossary_again = asyncio.run(engine.extract_project_glossary(project_id, session, config))
            glossary_forced = asyncio.run(engine.extract_project_glossary(project_id, session, config, force=True))

            self.assertTrue(glossary["auto_extract_completed"])
            self.assertTrue(glossary_again["auto_extract_completed"])
            self.assertTrue(glossary_forced["auto_extract_completed"])
            self.assertEqual(calls, 4)

    def test_project_glossary_extraction_uses_fallback_for_translation_only_doubao_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)

            captured: dict[str, str] = {}

            def fake_chat_text(**kwargs):
                captured["model"] = kwargs["model"]
                captured["user_prompt"] = kwargs["user_prompt"]
                return '[{"source":"小夏","translation":"小夏","category":"人名"}]'

            engine._request_chat_completions_text_sync = fake_chat_text  # type: ignore[method-assign]
            result = asyncio.run(engine._request_project_glossary_extraction({
                "translator": "custom_openai",
                "selected_translator": "doubao-ark",
                "translator_model": "doubao-seed-translation-250915",
                "api_key": "secret",
            }, "项目 OCR 原文"))

            self.assertIn("小夏", result)
            self.assertEqual(captured["model"], engine.DOUBAO_GLOSSARY_FALLBACK_MODEL)
            self.assertIn("项目 OCR 原文", captured["user_prompt"])

    def test_project_glossary_extraction_reports_missing_ocr_context(self) -> None:
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

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            glossary = asyncio.run(engine.extract_project_glossary(project_id, session, {
                "translator": "openai-compatible",
                "selected_translator": "openai-compatible",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "model",
                "api_key": "secret",
                "target_lang": "CHS",
            }, force=True))

            self.assertEqual(glossary["entries"], [])
            self.assertIn("先识别文本框", glossary["extract_message"])

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

    def test_resume_translation_skips_completed_pages_and_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            project_id = "project-resume"
            source_dir = root / "source"
            output_dir = root / "translated"
            source_dir.mkdir()
            output_dir.mkdir()
            Image.new("RGB", (8, 8), (255, 255, 255)).save(source_dir / "page-1.png")
            Image.new("RGB", (8, 8), (240, 240, 240)).save(source_dir / "page-2.png")
            Image.new("RGB", (8, 8), (12, 34, 56)).save(output_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(output_dir),
                "source_images": [
                    {"name": "page-1.png", "stored_name": "page-1.png"},
                    {"name": "page-2.png", "stored_name": "page-2.png"},
                ],
                "translated_output_map": {"page-1.png": "page-1.png"},
                "download_path": "",
                "workflow_stage": "detected",
                "last_config": {"rerender_output_format": "png"},
                "project_glossary": {"entries": [], "auto_extract_completed": True},
                "translation_region_overrides": {},
                "translation_region_skip_overrides": {},
                "translation_region_disabled_overrides": {},
                "translation_region_layout_overrides": {},
                "style_region_overrides": {},
            }
            rendered_pages: list[str] = []
            persisted_pages: list[list[str] | None] = []
            events: list[dict[str, object]] = []

            async def fake_translate_regions(*_args, **_kwargs) -> None:
                return None

            async def fake_render_cached_page(*_args, **kwargs) -> None:
                output_path = kwargs.get("output_path") if "output_path" in kwargs else _args[1]
                rendered_pages.append(Path(output_path).name)
                Image.new("RGB", (8, 8), (90, 90, 90)).save(output_path)

            async def collect_event(event: dict[str, object]) -> None:
                events.append(event)

            def fake_persist_project_state(_project_id, _session, **kwargs) -> None:
                persisted_pages.append(kwargs.get("page_ids"))

            def fake_archive(*_args, **_kwargs) -> str:
                archive_path = root / "translated.zip"
                archive_path.write_bytes(b"zip")
                return str(archive_path)

            engine._ensure_runtime_patches = lambda: None  # type: ignore[method-assign]
            engine._ensure_editable_page_cache = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
            engine._prepare_cached_regions_for_edit = lambda *_args, **_kwargs: []  # type: ignore[method-assign]
            engine._translate_cached_regions = fake_translate_regions  # type: ignore[method-assign]
            engine._persist_translated_regions = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
            engine._render_cached_page = fake_render_cached_page  # type: ignore[method-assign]
            engine.persist_project_state = fake_persist_project_state  # type: ignore[method-assign]
            engine.build_session_archive = fake_archive  # type: ignore[method-assign]

            result = asyncio.run(engine.resume_translation_session(
                session_id=project_id,
                session=session,
                raw_config={"rerender_output_format": "png"},
                progress_callback=collect_event,
                skip_completed=True,
            ))

            self.assertEqual(rendered_pages, ["page-2.png"])
            self.assertEqual(session["translated_output_map"]["page-1.png"], "page-1.png")
            self.assertEqual(session["translated_output_map"]["page-2.png"], "page-2.png")
            self.assertIn(["page-2.png"], persisted_pages)
            self.assertEqual(session["workflow_stage"], "translated")
            self.assertTrue(result["download_path"].endswith("translated.zip"))
            start_events = [event for event in events if event.get("event") == "start"]
            self.assertEqual(start_events[0]["total_pages"], 1)

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

    def test_seedream_request_size_rounds_up_to_min_pixels(self) -> None:
        client = SeedreamImageCleanupClient(api_key="secret", model="seedream-test")
        source = np.full((1400, 900, 3), 255, dtype=np.uint8)

        prepared_source, prepared_guide, size_value = client._prepare_request_images(source, None)

        width, height = [int(part) for part in size_value.split("x")]
        self.assertIsNone(prepared_guide)
        self.assertGreaterEqual(width * height, client.MIN_PIXELS)
        self.assertEqual(prepared_source.shape[:2], (height, width))

    def test_advanced_erase_region_mask_limits_full_page_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((80, 80, 3), 255, dtype=np.uint8)
            edited = np.full((80, 80, 3), 120, dtype=np.uint8)
            allowed_mask = np.zeros((80, 80), dtype=np.uint8)
            allowed_mask[30:50, 20:60] = 255
            final_mask = engine._advanced_erase_final_mask(
                engine._build_advanced_erase_change_mask(source, edited),
                allowed_mask,
            )

            composite, mask, changed_ratio = engine._composite_advanced_erase_result(
                source,
                edited,
                change_mask=final_mask,
            )

            self.assertGreater(int(mask[40, 30]), 0)
            self.assertEqual(int(mask[10, 10]), 0)
            self.assertLess(changed_ratio, engine.ADVANCED_ERASE_MAX_CHANGED_RATIO)
            self.assertTrue(np.array_equal(composite[10, 10], source[10, 10]))
            self.assertEqual(int(composite[40, 30, 0]), 120)

    def test_advanced_erase_region_mask_uses_clean_result_without_source_bleed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((80, 80, 3), 255, dtype=np.uint8)
            source[36:44, 30:50] = 0
            edited = np.full((80, 80, 3), 255, dtype=np.uint8)
            allowed_mask = np.zeros((80, 80), dtype=np.uint8)
            allowed_mask[30:50, 20:60] = 255
            final_mask = engine._advanced_erase_final_mask(
                engine._build_advanced_erase_change_mask(source, edited),
                allowed_mask,
            )

            composite, mask, changed_ratio = engine._composite_advanced_erase_result(
                source,
                edited,
                change_mask=final_mask,
            )

            self.assertGreater(changed_ratio, 0)
            self.assertEqual(int(mask[40, 40]), 255)
            self.assertTrue(np.array_equal(composite[40, 40], edited[40, 40]))
            self.assertTrue(np.array_equal(composite[10, 10], source[10, 10]))

    def test_selection_erase_input_blanks_outside_rects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            base = np.zeros((80, 80, 3), dtype=np.uint8)
            base[:, :] = [32, 96, 160]
            base[20:40, 10:30] = [0, 0, 0]

            rects = engine._normalize_selection_erase_rects(
                [{"x": 0.125, "y": 0.25, "width": 0.25, "height": 0.25}],
                base.shape,
            )
            mask = engine._build_selection_erase_mask(rects, base.shape)
            selected_input = engine._build_selection_erase_input_image(base, mask)

            self.assertEqual(rects, [(10, 20, 30, 40)])
            self.assertTrue(np.array_equal(selected_input[5, 5], [255, 255, 255]))
            self.assertTrue(np.array_equal(selected_input[25, 15], base[25, 15]))

    def test_selection_erase_composite_keeps_pixels_outside_rects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            base = np.zeros((80, 80, 3), dtype=np.uint8)
            base[:, :] = [210, 210, 210]
            base[24:28, 14:18] = [0, 0, 0]
            edited = np.full((80, 80, 3), 240, dtype=np.uint8)
            mask = np.zeros((80, 80), dtype=np.uint8)
            mask[20:40, 10:30] = 255

            composite, changed_ratio, precise_mask, model_change_mask, text_mask, residual_mask = (
                engine._composite_selection_erase_result(base, edited, mask)
            )

            self.assertGreater(changed_ratio, 0)
            self.assertGreater(int(cv2.countNonZero(precise_mask)), 0)
            self.assertGreater(int(cv2.countNonZero(model_change_mask)), 0)
            self.assertGreater(int(cv2.countNonZero(text_mask)), 0)
            self.assertEqual(int(cv2.countNonZero(residual_mask)), 0)
            self.assertTrue(np.array_equal(composite[5, 5], base[5, 5]))
            self.assertTrue(np.array_equal(composite[25, 15], edited[25, 15]))
            self.assertFalse(np.array_equal(composite[30, 25], base[30, 25]))

    def test_selection_erase_composite_inpaints_unchanged_symbol(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            base = np.full((80, 80, 3), 245, dtype=np.uint8)
            cv2.line(base, (24, 26), (30, 34), (0, 0, 0), 2)
            cv2.line(base, (36, 26), (30, 34), (0, 0, 0), 2)
            edited = base.copy()
            mask = np.zeros((80, 80), dtype=np.uint8)
            mask[18:44, 18:44] = 255

            composite, changed_ratio, precise_mask, model_change_mask, text_mask, residual_mask = (
                engine._composite_selection_erase_result(base, edited, mask)
            )

            self.assertGreater(changed_ratio, 0)
            self.assertEqual(int(cv2.countNonZero(model_change_mask)), 0)
            self.assertGreater(int(cv2.countNonZero(text_mask)), 0)
            self.assertGreater(int(cv2.countNonZero(residual_mask)), 0)
            self.assertGreater(int(composite[30, 30, 0]), 150)
            self.assertTrue(np.array_equal(composite[5, 5], base[5, 5]))

    def test_selection_erase_page_sends_only_selected_area_to_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            source = np.full((80, 80, 3), [64, 64, 64], dtype=np.uint8)
            Image.fromarray(source).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }
            cache_dir = engine._session_page_cache_dir(session, "project-a", "page-1.png")
            cache_dir.mkdir(parents=True)
            base = np.full((80, 80, 3), [220, 220, 220], dtype=np.uint8)
            base[24:28, 14:18] = [0, 0, 0]
            Image.fromarray(base).save(cache_dir / "inpainted.png")
            observed_inputs: list[np.ndarray] = []
            observed_prompts: list[str] = []

            class FakeClient:
                async def remove_text(self, source_rgb, _guide_rgb=None, prompt="", **_kwargs):
                    observed_inputs.append(source_rgb.copy())
                    observed_prompts.append(prompt)
                    edited = source_rgb.copy()
                    edited[20:40, 10:30] = [245, 245, 245]
                    return edited

            original_factory = translator_module.create_image_cleanup_client
            translator_module.create_image_cleanup_client = lambda **_kwargs: FakeClient()
            try:
                result = asyncio.run(engine.advanced_erase_page(
                    project_id="project-a",
                    session=session,
                    page_id="page-1.png",
                    raw_config={
                        "advanced_erase_provider": "volcengine-ark",
                        "advanced_erase_base_url": "https://ark.example.com/api/v3/images/generations",
                        "advanced_erase_model": "custom-seedream-model",
                        "advanced_erase_api_key": "secret",
                        "advanced_erase_selection_prompt": "custom selection prompt",
                    },
                    action="selection",
                    selections=[{"x": 0.125, "y": 0.25, "width": 0.25, "height": 0.25}],
                ))
            finally:
                translator_module.create_image_cleanup_client = original_factory

            self.assertEqual(result["advanced_erase"]["action"], "selection")
            self.assertEqual(len(observed_inputs), 1)
            self.assertEqual(observed_prompts, ["custom selection prompt"])
            self.assertTrue(np.array_equal(observed_inputs[0][5, 5], [255, 255, 255]))
            self.assertTrue(np.array_equal(observed_inputs[0][25, 15], base[25, 15]))
            output = np.array(Image.open(cache_dir / "inpainted.png").convert("RGB"))
            self.assertTrue(np.array_equal(output[5, 5], base[5, 5]))
            self.assertTrue(np.array_equal(output[25, 15], [245, 245, 245]))
            self.assertFalse(np.array_equal(output[38, 28], base[38, 28]))

    def test_local_model_selection_erase_does_not_require_remote_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            source = np.full((80, 80, 3), [64, 64, 64], dtype=np.uint8)
            Image.fromarray(source).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }
            cache_dir = engine._session_page_cache_dir(session, "project-a", "page-1.png")
            cache_dir.mkdir(parents=True)
            base = np.full((80, 80, 3), [220, 220, 220], dtype=np.uint8)
            base[24:28, 14:18] = [0, 0, 0]
            Image.fromarray(base).save(cache_dir / "inpainted.png")
            observed_masks: list[np.ndarray] = []

            async def fake_lama(base_rgb, selection_mask, *, device):
                observed_masks.append(selection_mask.copy())
                edited = base_rgb.copy()
                edited[selection_mask > 0] = [245, 245, 245]
                return edited

            engine._select_local_inpainting_device = lambda _use_gpu: "cpu"
            engine._run_local_lama_inpaint = fake_lama

            result = asyncio.run(engine.advanced_erase_page(
                project_id="project-a",
                session=session,
                page_id="page-1.png",
                raw_config={},
                action="local-selection",
                selections=[{"x": 0.125, "y": 0.25, "width": 0.25, "height": 0.25}],
            ))

            self.assertEqual(result["advanced_erase"]["action"], "local-selection")
            self.assertEqual(result["advanced_erase"]["model"], "lama_large")
            self.assertEqual(result["advanced_erase"]["device"], "cpu")
            self.assertEqual(len(observed_masks), 1)
            self.assertEqual(int(observed_masks[0][25, 15]), 255)
            self.assertEqual(int(observed_masks[0][5, 5]), 0)
            output = np.array(Image.open(cache_dir / "inpainted.png").convert("RGB"))
            self.assertTrue(np.array_equal(output[5, 5], base[5, 5]))
            self.assertFalse(np.array_equal(output[25, 15], base[25, 15]))

    def test_local_model_selection_erase_default_mask_preserves_bubble_outline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            source = np.full((120, 120, 3), 220, dtype=np.uint8)
            Image.fromarray(source).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }
            cache_dir = engine._session_page_cache_dir(session, "project-a", "page-1.png")
            cache_dir.mkdir(parents=True)
            base = np.full((120, 120, 3), 220, dtype=np.uint8)
            cv2.ellipse(base, (60, 60), (25, 40), 0, 0, 360, (255, 255, 255), -1)
            cv2.ellipse(base, (60, 60), (25, 40), 0, 0, 360, (0, 0, 0), 2)
            base[54:66, 56:64] = 0
            Image.fromarray(base).save(cache_dir / "inpainted.png")
            observed_masks: list[np.ndarray] = []

            async def fake_lama(base_rgb, selection_mask, *, device):
                observed_masks.append(selection_mask.copy())
                edited = base_rgb.copy()
                edited[selection_mask > 0] = [245, 245, 245]
                return edited

            engine._select_local_inpainting_device = lambda _use_gpu: "cpu"
            engine._run_local_lama_inpaint = fake_lama

            result = asyncio.run(engine.advanced_erase_page(
                project_id="project-a",
                session=session,
                page_id="page-1.png",
                raw_config={},
                action="local-selection",
                selections=[{"x": 0.25, "y": 0.12, "width": 0.5, "height": 0.76}],
            ))

            self.assertEqual(result["advanced_erase"]["mask_mode"], "text")
            self.assertEqual(len(observed_masks), 1)
            self.assertEqual(int(observed_masks[0][60, 60]), 255)
            self.assertEqual(int(observed_masks[0][20, 60]), 0)
            output = np.array(Image.open(cache_dir / "inpainted.png").convert("RGB"))
            self.assertTrue(np.array_equal(output[20, 60], base[20, 60]))
            self.assertFalse(np.array_equal(output[60, 60], base[60, 60]))

    def test_advanced_erase_allowed_mask_expands_to_white_bubble(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((240, 240, 3), 128, dtype=np.uint8)
            cv2.ellipse(source, (120, 120), (30, 48), 0, 0, 360, (255, 255, 255), -1)
            cv2.ellipse(source, (120, 120), (30, 48), 0, 0, 360, (0, 0, 0), 2)
            source[110:122, 116:124] = 0
            region = type("Region", (), {})()
            region.xyxy = [116, 108, 124, 126]
            region.font_size = 12

            mask = engine._build_advanced_erase_region_container_mask(source, region)

            self.assertGreater(int(mask[80, 120]), 0)
            self.assertGreater(int(mask[116, 120]), 0)
            self.assertEqual(int(mask[5, 5]), 0)

    def test_advanced_erase_allowed_mask_expands_to_line_art_container(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((240, 240, 3), 112, dtype=np.uint8)
            frame = np.array([[92, 60], [150, 58], [154, 138], [96, 142], [90, 112]], dtype=np.int32)
            cv2.polylines(source, [frame], isClosed=True, color=(245, 245, 245), thickness=3)
            source[90:106, 116:126] = 245
            region = type("Region", (), {})()
            region.xyxy = [116, 88, 126, 108]
            region.font_size = 14
            region.font_style = "sfx"

            mask = engine._build_advanced_erase_region_container_mask(source, region)

            self.assertGreater(int(mask[68, 100]), 0)
            self.assertGreater(int(mask[130, 144]), 0)
            self.assertEqual(int(mask[8, 8]), 0)

    def test_advanced_erase_line_art_container_requires_decorative_style(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((240, 240, 3), 112, dtype=np.uint8)
            frame = np.array([[92, 60], [150, 58], [154, 138], [96, 142], [90, 112]], dtype=np.int32)
            cv2.polylines(source, [frame], isClosed=True, color=(245, 245, 245), thickness=3)
            source[90:106, 116:126] = 245
            region = type("Region", (), {})()
            region.xyxy = [116, 88, 126, 108]
            region.font_size = 14
            region.font_style = "gothic"

            mask = engine._build_advanced_erase_region_container_mask(source, region)

            self.assertEqual(int(mask[68, 100]), 0)
            self.assertGreater(int(mask[98, 120]), 0)

    def test_advanced_erase_overbroad_allowed_mask_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            mask = np.zeros((100, 100), dtype=np.uint8)
            mask[:, :] = 255

            self.assertTrue(engine._advanced_erase_allowed_mask_is_overbroad(mask))

    def test_advanced_erase_model_container_mask_extracts_segmentation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((240, 240, 3), 128, dtype=np.uint8)
            marker = np.zeros((240, 240, 3), dtype=np.uint8)
            marker[20:220, 20:220] = 18
            cv2.ellipse(marker, (78, 84), (28, 42), 0, 0, 360, (0, 255, 0), -1)
            frame = np.array([[132, 76], [186, 70], [196, 142], [126, 152], [118, 112]], dtype=np.int32)
            cv2.fillPoly(marker, [frame], (0, 255, 0))

            mask, count = engine._build_advanced_erase_model_container_mask(source, marker)

            self.assertIsNotNone(mask)
            assert mask is not None
            self.assertGreaterEqual(count, 2)
            self.assertGreater(int(mask[84, 78]), 0)
            self.assertGreater(int(mask[120, 150]), 0)
            self.assertEqual(int(mask[10, 10]), 0)

    def test_advanced_erase_model_container_mask_accepts_legacy_white_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((160, 160, 3), 96, dtype=np.uint8)
            marker = np.zeros((160, 160, 3), dtype=np.uint8)
            cv2.rectangle(marker, (52, 42), (108, 118), (255, 255, 255), -1)

            mask, count = engine._build_advanced_erase_model_container_mask(source, marker)

            self.assertIsNotNone(mask)
            assert mask is not None
            self.assertEqual(count, 1)
            self.assertGreater(int(mask[72, 80]), 0)
            self.assertEqual(int(mask[12, 12]), 0)

    def test_advanced_erase_model_container_mask_handles_dark_containers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((200, 200, 3), 180, dtype=np.uint8)
            cv2.rectangle(source, (66, 54), (134, 146), (8, 8, 8), -1)
            cv2.rectangle(source, (66, 54), (134, 146), (245, 245, 245), 2)
            marker = np.zeros((200, 200, 3), dtype=np.uint8)
            cv2.rectangle(marker, (66, 54), (134, 146), (0, 255, 0), -1)

            mask, count = engine._build_advanced_erase_model_container_mask(source, marker)

            self.assertIsNotNone(mask)
            assert mask is not None
            self.assertEqual(count, 1)
            self.assertGreater(int(mask[88, 90]), 0)

    def test_advanced_erase_model_container_mask_rejects_full_page_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((100, 100, 3), 128, dtype=np.uint8)
            marker = np.full((100, 100, 3), 255, dtype=np.uint8)

            mask, count = engine._build_advanced_erase_model_container_mask(source, marker)

            self.assertIsNone(mask)
            self.assertEqual(count, 0)

    def test_advanced_erase_white_container_residue_is_cleaned_selectively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            source = np.full((180, 180, 3), 120, dtype=np.uint8)
            cv2.ellipse(source, (58, 72), (30, 46), 0, 0, 360, (255, 255, 255), -1)
            cv2.ellipse(source, (58, 72), (30, 46), 0, 0, 360, (0, 0, 0), 2)
            source[62:70, 50:66] = 0
            texture_frame = np.array([[104, 48], [150, 42], [158, 126], [102, 132]], dtype=np.int32)
            cv2.fillPoly(source, [texture_frame], (132, 132, 132))

            composite = source.copy()
            composite[62:70, 50:66] = 210
            cv2.fillPoly(composite, [texture_frame], (164, 164, 164))
            mask = np.zeros((180, 180), dtype=np.uint8)
            cv2.ellipse(mask, (58, 72), (30, 46), 0, 0, 360, 255, -1)
            cv2.fillPoly(mask, [texture_frame], 255)

            cleaned = engine._clean_advanced_erase_white_container_residue(source, composite, mask)

            self.assertGreater(int(cleaned[66, 58, 0]), 240)
            self.assertLess(int(cleaned[72, 28, 0]), 32)
            self.assertEqual(int(cleaned[86, 130, 0]), 164)

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

    def test_persisted_settings_redact_and_preserve_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            saved = engine.save_persisted_settings({
                "translator": "gemini",
                "api_key": "top-secret",
            })
            self.assertEqual(saved["api_key"], "")
            self.assertTrue(saved["configured_secrets"]["api_key"])
            self.assertEqual(engine.paths.load_settings()["api_key"], "top-secret")

            updated = engine.save_persisted_settings({
                "target_lang": "ENG",
                "api_key": "",
            })
            self.assertEqual(updated["api_key"], "")
            self.assertTrue(updated["configured_secrets"]["api_key"])
            self.assertEqual(engine.paths.load_settings()["api_key"], "top-secret")
            self.assertEqual(engine.normalize_user_config({})["api_key"], "top-secret")

    def test_openai_compatible_settings_survive_save_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))

            engine.save_persisted_settings({
                "translator": "openai-compatible",
                "openai_base_url": "https://api.example.com/v1",
                "openai_model": "example-model",
                "api_key": "top-secret",
            })
            reloaded = engine.load_persisted_settings()

            self.assertEqual(reloaded["translator"], "openai-compatible")
            self.assertEqual(reloaded["selected_translator"], "openai-compatible")
            self.assertEqual(reloaded["openai_base_url"], "https://api.example.com/v1")
            self.assertEqual(reloaded["openai_model"], "example-model")
            self.assertTrue(reloaded["configured_secrets"]["api_key"])

    def test_persisted_settings_require_explicit_secret_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            engine.save_persisted_settings({"api_key": "top-secret"})

            cleared = engine.save_persisted_settings({
                "api_key": "",
                "_clear_secrets": ["api_key"],
            })

            self.assertFalse(cleared["configured_secrets"]["api_key"])
            self.assertEqual(engine.paths.load_settings()["api_key"], "")

    @unittest.skipIf(os.name == "nt", "POSIX file modes are not portable to Windows")
    def test_persisted_settings_file_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = self.make_engine(Path(tmp))
            engine.save_persisted_settings({"api_key": "top-secret"})

            mode = engine.paths.settings_path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_default_font_mapping_uses_bundled_font(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / "backend"
            system_font_dir = root / "fonts" / "system"
            system_font_dir.mkdir(parents=True)
            test_font = system_font_dir / "SourceHanSansSC-Regular-2.otf"
            test_font.write_bytes(b"test-font")
            engine = TranslatorEngine(base_dir, app_paths=make_test_paths(root))

            config = engine.normalize_user_config({})

            self.assertEqual(config["font_style_mode"], "auto-map")
            self.assertEqual(config["font_key"], engine.DEFAULT_FONT_KEY)
            for style in engine.STYLE_BUCKETS:
                self.assertEqual(config["style_font_keys"][style], engine.DEFAULT_FONT_KEY)
                self.assertTrue(config["style_font_paths"][style].endswith("SourceHanSansSC-Regular-2.otf"))

    def test_font_mapping_keeps_bundled_style_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / "backend"
            bundled_font_dir = root / "fonts" / "system"
            custom_font_dir = root / "fonts" / "custom"
            bundled_font_dir.mkdir(parents=True)
            custom_font_dir.mkdir(parents=True)
            (bundled_font_dir / "SourceHanSansSC-Regular-2.otf").write_bytes(b"bundled-font")
            custom_font = custom_font_dir / "CustomDialogue.otf"
            custom_font.write_bytes(b"bundled-font")
            engine = TranslatorEngine(base_dir, app_paths=make_test_paths(root))

            config = engine.normalize_user_config({
                "style_font_gothic_key": f"project:{custom_font.name}",
                "style_font_sfx_key": "",
            })

            self.assertEqual(config["style_font_keys"]["gothic"], f"project:{custom_font.name}")
            self.assertEqual(config["style_font_keys"]["sfx"], engine.DEFAULT_FONT_KEY)
            self.assertEqual(Path(config["style_font_paths"]["gothic"]).name, custom_font.name)

    def test_font_mapping_rejects_arbitrary_local_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / "backend"
            system_font_dir = root / "fonts" / "system"
            system_font_dir.mkdir(parents=True)
            default_font = system_font_dir / "SourceHanSansSC-Regular-2.otf"
            default_font.write_bytes(b"bundled-font")
            outside_font = root / "outside.otf"
            outside_font.write_bytes(b"outside-font")
            engine = TranslatorEngine(base_dir, app_paths=make_test_paths(root))

            config = engine.normalize_user_config({
                "font_key": str(outside_font),
                "style_font_gothic_key": f"project:../{outside_font.name}",
            })

            self.assertEqual(config["font_key"], engine.DEFAULT_FONT_KEY)
            self.assertEqual(Path(config["font_path"]).name, default_font.name)
            self.assertEqual(config["style_font_keys"]["gothic"], engine.DEFAULT_FONT_KEY)
            self.assertEqual(Path(config["style_font_paths"]["gothic"]).name, default_font.name)

    def test_recent_project_prefixed_preset_font_key_moves_back_to_system(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_dir = root / "backend"
            system_font_dir = root / "fonts" / "system"
            system_font_dir.mkdir(parents=True)
            preset = system_font_dir / "SourceHanSansSC-Medium-2.otf"
            preset.write_bytes(b"bundled-font")
            engine = TranslatorEngine(base_dir, app_paths=make_test_paths(root))

            config = engine.normalize_user_config({
                "font_key": f"project:{preset.name}",
                "style_font_gothic_key": f"project:{preset.name}",
            })

            expected_key = f"system:{preset.name}"
            self.assertEqual(config["font_key"], expected_key)
            self.assertEqual(config["style_font_keys"]["gothic"], expected_key)
            self.assertEqual(Path(config["font_path"]).name, preset.name)

    def test_advanced_erase_rejection_saves_debug_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = self.make_engine(root)
            source_dir = root / "source"
            translated_dir = root / "translated"
            source_dir.mkdir()
            translated_dir.mkdir()
            Image.new("RGB", (80, 80), (255, 255, 255)).save(source_dir / "page-1.png")
            session = {
                "source_dir": str(source_dir),
                "translated_dir": str(translated_dir),
                "source_images": [{"name": "page-1.png", "stored_name": "page-1.png"}],
                "last_config": {},
            }

            class FakeClient:
                async def remove_text(self, *_args, **_kwargs):
                    return np.full((80, 80, 3), 120, dtype=np.uint8)

            original_factory = translator_module.create_image_cleanup_client
            translator_module.create_image_cleanup_client = lambda **_kwargs: FakeClient()
            try:
                with self.assertRaisesRegex(RuntimeError, "调试文件已保存到"):
                    asyncio.run(engine.advanced_erase_page(
                        project_id="project-a",
                        session=session,
                        page_id="page-1.png",
                        raw_config={
                            "advanced_erase_provider": "volcengine-ark",
                            "advanced_erase_base_url": "https://ark.example.com/api/v3/images/generations",
                            "advanced_erase_model": "custom-seedream-model",
                            "advanced_erase_api_key": "secret",
                        },
                    ))
            finally:
                translator_module.create_image_cleanup_client = original_factory

            attempt_dir = engine._advanced_erase_attempt_dir(
                engine._session_page_cache_dir(session, "project-a", "page-1.png")
            )
            input_images = list(attempt_dir.glob("*.input.png"))
            seedream_outputs = list(attempt_dir.glob("*.seedream.png"))
            diff_outputs = list(attempt_dir.glob("*.diff.png"))
            mask_outputs = list(attempt_dir.glob("*.mask.png"))
            metadata_outputs = list(attempt_dir.glob("*.json"))
            self.assertEqual(len(input_images), 1)
            self.assertEqual(len(seedream_outputs), 1)
            self.assertEqual(len(diff_outputs), 1)
            self.assertEqual(len(mask_outputs), 1)
            self.assertEqual(len(metadata_outputs), 1)
            metadata = json.loads(metadata_outputs[0].read_text(encoding="utf-8"))
            self.assertTrue(metadata["rejected"])
            self.assertGreater(metadata["changed_ratio"], engine.ADVANCED_ERASE_MAX_CHANGED_RATIO)
            self.assertEqual(Path(metadata["input_image"]).name, input_images[0].name)
            self.assertEqual(Path(metadata["seedream_output"]).name, seedream_outputs[0].name)
            self.assertEqual(Path(metadata["diff_mask"]).name, diff_outputs[0].name)
            self.assertEqual(Path(metadata["final_mask"]).name, mask_outputs[0].name)


if __name__ == "__main__":
    unittest.main()
