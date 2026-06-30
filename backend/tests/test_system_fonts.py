from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from system_fonts import ensure_project_font_directories


class SystemFontsTests(unittest.TestCase):
    def test_legacy_flat_fonts_move_to_custom_and_duplicate_presets_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "fonts"
            system_dir = root / "system"
            system_dir.mkdir(parents=True)
            preset = system_dir / "SourceHanSansSC-Regular-2.otf"
            preset.write_bytes(b"preset-font")
            (root / preset.name).write_bytes(b"preset-font")
            (root / "MyCustomFont.otf").write_bytes(b"custom-font")

            with mock.patch.dict(os.environ, {"APP_FONT_DIR": str(root)}):
                resolved_root = ensure_project_font_directories(BACKEND_DIR)

            self.assertEqual(resolved_root, root.resolve())
            self.assertFalse((root / preset.name).exists())
            self.assertTrue((root / "custom" / "MyCustomFont.otf").exists())


if __name__ == "__main__":
    unittest.main()
