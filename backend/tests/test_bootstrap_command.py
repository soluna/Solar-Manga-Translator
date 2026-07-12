from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]


class BootstrapCommandTests(unittest.TestCase):
    def test_silent_command_reports_heartbeat_and_mirrors_output_to_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "bootstrap.log"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(BACKEND_DIR / "bootstrap_command.py"),
                    "--label",
                    "后端依赖安装",
                    "--log",
                    str(log_path),
                    "--heartbeat-seconds",
                    "0.05",
                    "--",
                    sys.executable,
                    "-c",
                    (
                        "import time; "
                        "time.sleep(0.18); "
                        "print('child command complete', flush=True)"
                    ),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("仍在运行", completed.stdout)
            self.assertIn("已完成", completed.stdout)
            self.assertIn("child command complete", completed.stdout)
            self.assertIn(
                "child command complete",
                log_path.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
