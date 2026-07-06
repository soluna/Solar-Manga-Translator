from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pip_install


class PipInstallTests(unittest.TestCase):
    def test_install_falls_back_to_official_index(self) -> None:
        calls: list[list[str]] = []

        def run(command, check):
            self.assertTrue(check)
            calls.append(command)
            if len(calls) == 1:
                raise subprocess.CalledProcessError(1, command)
            return mock.Mock(returncode=0)

        with (
            mock.patch.dict(os.environ, {"MT_PIP_INDEXES": "https://mirror.invalid/simple,https://pypi.org/simple"}, clear=False),
            mock.patch.object(pip_install.subprocess, "run", side_effect=run),
        ):
            pip_install.install_with_fallback(["-r", "requirements.txt"])

        self.assertIn("https://mirror.invalid/simple", calls[0])
        self.assertIn("https://pypi.org/simple", calls[1])

    def test_configured_indexes_are_deduplicated(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"MT_PIP_INDEXES": "https://a.invalid/simple,https://a.invalid/simple,https://b.invalid/simple"},
            clear=False,
        ):
            self.assertEqual(
                pip_install.pip_indexes(),
                ["https://a.invalid/simple", "https://b.invalid/simple"],
            )


if __name__ == "__main__":
    unittest.main()
