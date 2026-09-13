from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from backend.tests import test_page_region_commands as fixtures
from backend.tests._textblock_stub import textblock_module_patch


class BrushCoordinateTests(unittest.TestCase):
    def test_replacement_does_not_choose_arbitrarily_between_duplicate_page_names(self):
        with tempfile.TemporaryDirectory() as temporary, textblock_module_patch():
            root = Path(temporary)
            engine, project_id, page_id, session = fixtures.PageRegionCommandTests().make_project(root)
            session["source_images"] = [
                {"name": "Page 1.png", "stored_name": page_id},
                {"name": "Page 1.png", "stored_name": "0002.png"},
            ]
            Image.new("RGB", (120, 160), "white").save(Path(session["source_dir"]) / "0002.png")
            session["artifact_state"] = fixtures.ProjectArtifactState.create([page_id, "0002.png"]).model_dump(mode="json")
            replacement = root / "Page 1.png"
            Image.new("RGB", (120, 160), "black").save(replacement)
            with patch.object(engine, "_ensure_runtime_patches"):
                with self.assertRaisesRegex(ValueError, "匹配|同名"):
                    engine.attach_base_images(project_id, session, [str(replacement)])

    def test_normalized_brush_scales_on_replacement_blank_and_keeps_legacy_pixels(self):
        with tempfile.TemporaryDirectory() as temporary, textblock_module_patch():
            root = Path(temporary)
            engine, project_id, page_id, session = fixtures.PageRegionCommandTests().make_project(root)
            with patch.object(engine, "_ensure_runtime_patches"):
                replacement = root / "Page 1.png"
                Image.new("RGB", (480, 640), "white").save(replacement)
                engine.attach_base_images(project_id, session, [str(replacement)])
                engine.brush_edit_page(project_id, session, page_id, [
                    {"mode": "paint", "coordinate_space": "normalized", "size_space": "normalized",
                     "points": [{"x": 0.5, "y": 0.5}], "size": 0.2, "color": [16, 32, 48]},
                    {"mode": "paint", "coordinate_space": "pixel", "points": [{"x": 100, "y": 100}],
                     "size": 10, "color": [80, 90, 100]},
                ])
                result = Image.open(engine.get_page_base_image_path(project_id, session, page_id)).convert("RGB")
                self.assertEqual(result.size, (480, 640))
                self.assertEqual(result.getpixel((270, 320)), (16, 32, 48))
                self.assertEqual(result.getpixel((300, 320)), (255, 255, 255))
                self.assertEqual(result.getpixel((100, 100)), (80, 90, 100))
                self.assertEqual(result.getpixel((110, 100)), (255, 255, 255))


if __name__ == "__main__":
    unittest.main()
