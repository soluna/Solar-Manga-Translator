from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from diagnostics_bundle import build_diagnostics_zip


class DiagnosticsBundleTests(unittest.TestCase):
    def test_bundle_contains_logs_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs_dir = Path(tmp)
            (logs_dir / "backend.log").write_text(
                "backend started\nAuthorization: Bearer private-api-key\n",
                encoding="utf-8",
            )
            bundle = build_diagnostics_zip(
                diagnostics={"gpu": {"status": "torch_cpu_build"}},
                runtime={"logs_dir": str(logs_dir)},
                settings={
                    "api_key": "private-api-key",
                    "configured_secrets": {"api_key": True},
                },
                logs_dir=logs_dir,
            )

            self.assertNotIn(b"private-api-key", bundle)
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                self.assertIn("diagnostics.json", archive.namelist())
                self.assertIn("logs/backend.log", archive.namelist())
                diagnostics = json.loads(archive.read("diagnostics.json"))
                self.assertEqual(diagnostics["settings"]["api_key"], "[REDACTED]")
                self.assertTrue(diagnostics["settings"]["configured_secrets"]["api_key"])

    def test_bundle_removes_personal_paths_and_recognized_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs_dir = Path(tmp)
            (logs_dir / "backend.log").write_text(
                "\n".join(
                    [
                        "data_dir=C:\\Users\\SOLUNA\\AppData\\Local\\Solar-Manga-Translator",
                        "source=/Users/sol/private-manga/page-001.png",
                        "[Model48pxOCR] 私密漫画对白",
                        "OCR text: secret speech bubble",
                    ]
                ),
                encoding="utf-8",
            )
            bundle = build_diagnostics_zip(
                diagnostics={
                    "paths": {
                        "data_dir": "C:\\Users\\SOLUNA\\AppData\\Local\\Solar-Manga-Translator",
                    }
                },
                runtime={
                    "models_dir": "/Users/sol/Library/Application Support/Solar-Manga-Translator/models",
                },
                settings={},
                logs_dir=logs_dir,
            )

            self.assertNotIn(b"SOLUNA", bundle)
            self.assertNotIn(b"/Users/sol", bundle)
            self.assertNotIn("私密漫画对白".encode("utf-8"), bundle)
            self.assertNotIn(b"secret speech bubble", bundle)
            with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
                diagnostics = json.loads(archive.read("diagnostics.json"))
                self.assertEqual(
                    diagnostics["diagnostics"]["paths"]["data_dir"],
                    "[LOCAL_PATH]",
                )


if __name__ == "__main__":
    unittest.main()
