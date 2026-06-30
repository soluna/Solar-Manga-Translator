from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from utils.file_handler import extract_archive


def make_png_bytes(size: tuple[int, int] = (8, 8)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


class FileHandlerArchiveTests(unittest.TestCase):
    def test_extract_archive_extracts_valid_images_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "book.cbz"
            extract_dir = root / "extracted"

            with zipfile.ZipFile(archive, "w") as zip_file:
                zip_file.writestr("pages/0001.png", make_png_bytes())
                zip_file.writestr("notes/readme.txt", "not an image")

            images = extract_archive(str(archive), str(extract_dir))

            self.assertEqual(len(images), 1)
            self.assertTrue(images[0].endswith("pages/0001.png"))
            self.assertFalse((extract_dir / "notes" / "readme.txt").exists())

    def test_extract_archive_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "evil.cbz"

            with zipfile.ZipFile(archive, "w") as zip_file:
                zip_file.writestr("../escape.png", make_png_bytes())

            with self.assertRaises(ValueError):
                extract_archive(str(archive), str(root / "extracted"))

            self.assertFalse((root / "escape.png").exists())

    def test_extract_archive_rejects_too_many_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "too-many.cbz"

            with zipfile.ZipFile(archive, "w") as zip_file:
                zip_file.writestr("0001.png", make_png_bytes())
                zip_file.writestr("0002.png", make_png_bytes())

            with patch.dict(os.environ, {"MT_MAX_ARCHIVE_MEMBERS": "1"}):
                with self.assertRaises(ValueError):
                    extract_archive(str(archive), str(root / "extracted"))

    def test_extract_archive_rejects_abnormal_compression_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "ratio.cbz"

            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
                zip_file.writestr("0001.png", b"0" * 4096)

            with patch.dict(os.environ, {"MT_MAX_ARCHIVE_COMPRESSION_RATIO": "2"}):
                with self.assertRaises(ValueError):
                    extract_archive(str(archive), str(root / "extracted"))

    def test_extract_archive_discards_fake_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "fake.cbz"

            with zipfile.ZipFile(archive, "w") as zip_file:
                zip_file.writestr("0001.png", b"not-an-image")

            images = extract_archive(str(archive), str(root / "extracted"))

            self.assertEqual(images, [])
            self.assertFalse((root / "extracted" / "0001.png").exists())


if __name__ == "__main__":
    unittest.main()
