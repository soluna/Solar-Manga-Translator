from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from logging_config import configure_rotating_file_logging


class LoggingConfigTests(unittest.TestCase):
    def test_log_file_rotates_and_configuration_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "backend.log"
            logger = logging.getLogger(f"solar-test-{id(self)}")
            logger.handlers.clear()
            logger.propagate = False

            first = configure_rotating_file_logging(
                log_path,
                logger=logger,
                max_bytes=256,
                backup_count=2,
                include_stream=False,
            )
            second = configure_rotating_file_logging(
                log_path,
                logger=logger,
                max_bytes=256,
                backup_count=2,
                include_stream=False,
            )
            self.assertIs(first, second)
            self.assertEqual(len(logger.handlers), 1)

            for index in range(30):
                logger.info("diagnostic line %s %s", index, "x" * 40)
            for handler in logger.handlers:
                handler.flush()

            self.assertTrue(log_path.exists())
            self.assertTrue(log_path.with_name("backend.log.1").exists())


if __name__ == "__main__":
    unittest.main()
