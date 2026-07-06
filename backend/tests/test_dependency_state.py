from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import dependency_state


class DependencyStateTests(unittest.TestCase):
    def test_stamp_invalidates_when_dependency_input_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative_path in dependency_state.FRONTEND_FILES:
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative_path, encoding="utf-8")
            vite_package = root / "frontend" / "node_modules" / "vite" / "package.json"
            vite_package.parent.mkdir(parents=True)
            vite_package.write_text("{}", encoding="utf-8")
            stamp = root / "frontend" / "node_modules" / ".solar-dependencies.json"

            dependency_state.write_stamp(root, "frontend", stamp)
            self.assertTrue(dependency_state.stamp_matches(root, "frontend", stamp))

            (root / "frontend" / "package-lock.json").write_text("changed", encoding="utf-8")
            self.assertFalse(dependency_state.stamp_matches(root, "frontend", stamp))

    def test_missing_runtime_invalidates_matching_stamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for relative_path in dependency_state.FRONTEND_FILES:
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative_path, encoding="utf-8")
            stamp = root / "stamp.json"
            with mock.patch.object(dependency_state, "runtime_is_present", return_value=True):
                dependency_state.write_stamp(root, "frontend", stamp)

            self.assertFalse(dependency_state.stamp_matches(root, "frontend", stamp))


if __name__ == "__main__":
    unittest.main()
