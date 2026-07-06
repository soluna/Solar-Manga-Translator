from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import patched_model_download


class FakeResponse:
    status_code = 200
    headers = {"content-length": "4"}

    def __init__(self) -> None:
        self.closed = False

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield b"test"

    def close(self) -> None:
        self.closed = True


class ModelDownloadTests(unittest.TestCase):
    def test_github_ocr_prefers_huggingface_mirror_and_keeps_official_fallback(self) -> None:
        official = (
            "https://github.com/zyddnys/manga-image-translator/"
            "releases/download/beta-0.3/ocr_ar_48px.ckpt"
        )
        candidates = patched_model_download.model_download_candidates(official)

        self.assertTrue(candidates[0].startswith("https://hf-mirror.com/"))
        self.assertIn(
            "https://huggingface.co/zyddnys/manga-image-translator/resolve/main/ocr_ar_48px.ckpt",
            candidates,
        )
        self.assertEqual(candidates[-1], official)

    def test_github_detector_has_multiple_mirror_fallbacks(self) -> None:
        official = (
            "https://github.com/zyddnys/manga-image-translator/"
            "releases/download/beta-0.3/detect-20241225.ckpt"
        )
        candidates = patched_model_download.model_download_candidates(official)

        self.assertEqual(candidates[0], f"https://gh-proxy.com/{official}")
        self.assertIn(f"https://ghproxy.net/{official}", candidates)
        self.assertEqual(candidates[-1], official)

    def test_huggingface_lama_prefers_mainland_mirror(self) -> None:
        official = (
            "https://huggingface.co/dreMaz/AnimeMangaInpainting/"
            "resolve/main/lama_large_512px.ckpt"
        )
        candidates = patched_model_download.model_download_candidates(official)

        self.assertEqual(
            candidates[0],
            (
                "https://hf-mirror.com/dreMaz/AnimeMangaInpainting/"
                "resolve/main/lama_large_512px.ckpt"
            ),
        )
        self.assertEqual(candidates[-1], official)

    def test_download_uses_connect_and_read_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "model.bin"
            response = FakeResponse()
            with mock.patch.object(
                patched_model_download.requests,
                "get",
                return_value=response,
            ) as request:
                patched_model_download.download_url_with_progressbar(
                    "https://example.invalid/model.bin",
                    str(destination),
                )

            self.assertEqual(destination.read_bytes(), b"test")
            self.assertTrue(response.closed)
            self.assertEqual(request.call_args.kwargs["timeout"], (10.0, 30.0))

    def test_checksum_mismatch_switches_to_next_model_source(self) -> None:
        official = "https://github.com/example/releases/detect-20241225.ckpt"
        mirror = "https://mirror.invalid/detect-20241225.ckpt"
        expected_content = b"verified model"
        expected_sha256 = hashlib.sha256(expected_content).hexdigest()
        calls: list[str] = []

        def download(candidate: str, destination: Path) -> None:
            calls.append(candidate)
            destination.write_bytes(
                b"not a model" if candidate == mirror else expected_content
            )

        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "detect-20241225.ckpt.part"
            with (
                mock.patch.object(
                    patched_model_download,
                    "model_download_candidates",
                    return_value=[mirror, official],
                ),
                mock.patch.object(
                    patched_model_download,
                    "_download_once",
                    side_effect=download,
                ),
                mock.patch.dict(
                    patched_model_download.EXPECTED_MODEL_SHA256,
                    {"detect-20241225.ckpt": expected_sha256},
                ),
                mock.patch.object(patched_model_download.time, "sleep"),
            ):
                patched_model_download.download_url_with_progressbar(
                    official,
                    str(destination),
                )

            self.assertEqual(calls, [mirror, mirror, official])
            self.assertEqual(destination.read_bytes(), expected_content)


if __name__ == "__main__":
    unittest.main()
