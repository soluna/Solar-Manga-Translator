from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from model_manager import CORE_MODELS, build_model_readiness


class ModelManagerTests(unittest.TestCase):
    def test_readiness_lists_every_missing_core_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            readiness = build_model_readiness(Path(tmp))

        self.assertEqual(readiness["status"], "download_required")
        self.assertEqual(readiness["total_count"], len(CORE_MODELS))
        self.assertEqual(len(readiness["missing_ids"]), len(CORE_MODELS))

    def test_readiness_becomes_ready_when_manifest_files_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            models_dir = Path(tmp)
            for model in CORE_MODELS:
                path = models_dir / str(model["relative_path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"model")

            readiness = build_model_readiness(models_dir)

        self.assertEqual(readiness["status"], "ready")
        self.assertEqual(readiness["ready_count"], len(CORE_MODELS))


if __name__ == "__main__":
    unittest.main()
