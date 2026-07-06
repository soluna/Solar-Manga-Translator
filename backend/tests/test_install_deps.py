from __future__ import annotations

import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import install_deps
import patch_pydensecrf


class InstallDepsTests(unittest.TestCase):
    def git(self, repository: Path, *args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(repository), *args],
            text=True,
        ).strip()

    def create_upstream_repository(self, root: Path) -> tuple[Path, str, str]:
        repository = root / "upstream"
        subprocess.run(["git", "init", "-b", "main", str(repository)], check=True)
        self.git(repository, "config", "user.name", "Install Test")
        self.git(repository, "config", "user.email", "install-test@example.invalid")

        tracked_file = repository / "version.txt"
        tracked_file.write_text("pinned\n", encoding="utf-8")
        self.git(repository, "add", "version.txt")
        self.git(repository, "commit", "-m", "pinned")
        pinned_commit = self.git(repository, "rev-parse", "HEAD")

        tracked_file.write_text("newer\n", encoding="utf-8")
        self.git(repository, "commit", "-am", "newer")
        newer_commit = self.git(repository, "rev-parse", "HEAD")
        return repository, pinned_commit, newer_commit

    def test_fresh_checkout_fetches_only_pinned_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, pinned_commit, _newer_commit = self.create_upstream_repository(root)
            checkout = root / "checkout"

            with (
                mock.patch.object(install_deps, "UPSTREAM_CHECKOUT_DIR", checkout),
                mock.patch.object(
                    install_deps,
                    "load_upstream_metadata",
                    return_value={"repository": str(repository), "commit": pinned_commit},
                ),
                mock.patch.object(patch_pydensecrf, "patch_mask_refinement", return_value=True),
            ):
                install_deps.ensure_upstream_checkout()

            self.assertEqual(self.git(checkout, "rev-parse", "HEAD"), pinned_commit)
            self.assertEqual((checkout / "version.txt").read_text(encoding="utf-8"), "pinned\n")

    def test_existing_dirty_checkout_is_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, pinned_commit, newer_commit = self.create_upstream_repository(root)
            checkout = root / "checkout"

            with (
                mock.patch.object(install_deps, "UPSTREAM_CHECKOUT_DIR", checkout),
                mock.patch.object(
                    install_deps,
                    "load_upstream_metadata",
                    return_value={"repository": str(repository), "commit": pinned_commit},
                ),
                mock.patch.object(patch_pydensecrf, "patch_mask_refinement", return_value=True),
            ):
                install_deps.ensure_upstream_checkout()

            (checkout / "version.txt").write_text("local change\n", encoding="utf-8")
            with (
                mock.patch.object(install_deps, "UPSTREAM_CHECKOUT_DIR", checkout),
                mock.patch.object(
                    install_deps,
                    "load_upstream_metadata",
                    return_value={"repository": str(repository), "commit": newer_commit},
                ),
                mock.patch.object(patch_pydensecrf, "patch_mask_refinement", return_value=True),
            ):
                with self.assertRaisesRegex(RuntimeError, "local changes"):
                    install_deps.ensure_upstream_checkout()

            self.assertEqual(self.git(checkout, "rev-parse", "HEAD"), pinned_commit)
            self.assertEqual((checkout / "version.txt").read_text(encoding="utf-8"), "local change\n")

    def test_archive_fallback_rejects_traversal_and_installs_verified_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root = root / "source" / "manga-image-translator-pinned"
            source_root.mkdir(parents=True)
            (source_root / "version.txt").write_text("pinned\n", encoding="utf-8")
            archive_path = root / "upstream.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(source_root, arcname=source_root.name)

            checkout = root / "checkout"
            (checkout / ".git").mkdir(parents=True)
            install_deps._install_upstream_archive(archive_path, checkout, "abc123")

            self.assertEqual((checkout / "version.txt").read_text(encoding="utf-8"), "pinned\n")
            self.assertEqual(
                (checkout / install_deps.UPSTREAM_ARCHIVE_MARKER).read_text(encoding="utf-8").strip(),
                "abc123",
            )

    def test_github_archive_candidates_keep_official_source_last(self) -> None:
        urls = install_deps.upstream_archive_urls(
            "https://github.com/zyddnys/manga-image-translator.git",
            "abc123",
        )

        self.assertTrue(urls[0].startswith("https://gh-proxy.com/"))
        self.assertTrue(urls[1].startswith("https://ghproxy.net/"))
        self.assertEqual(
            urls[-1],
            "https://github.com/zyddnys/manga-image-translator/archive/abc123.tar.gz",
        )


if __name__ == "__main__":
    unittest.main()
