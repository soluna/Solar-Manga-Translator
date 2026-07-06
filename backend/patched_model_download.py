from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
import tqdm


DEFAULT_GITHUB_MIRRORS = (
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
    "https://ghfast.top/",
)
UPSTREAM_HUGGINGFACE_FILES = {
    "ocr_ar_48px.ckpt",
    "alphabet-all-v7.txt",
}
EXPECTED_MODEL_SHA256 = {
    "detect-20241225.ckpt": "67ce1c4ed4793860f038c71189ba9630a7756f7683b1ee5afb69ca0687dc502e",
    "ocr_ar_48px.ckpt": "29daa46d080818bb4ab239a518a88338cbccff8f901bef8c9db191a7cb97671d",
    "alphabet-all-v7.txt": "f5722368146aa0fbcc9f4726866e4efc3203318ebb66c811d8cbbe915576538a",
    "lama_large_512px.ckpt": "11d30fbb3000fb2eceae318b75d9ced9229d99ae990a7f8b3ac35c8d31f2c935",
}


class ModelChecksumError(OSError):
    pass


def _env_seconds(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(1.0, min(value, 300.0))


def _github_mirror_prefixes() -> tuple[str, ...]:
    raw_value = os.getenv("MT_GITHUB_MODEL_MIRRORS")
    if raw_value is None:
        return DEFAULT_GITHUB_MIRRORS
    return tuple(
        item.strip().rstrip("/") + "/"
        for item in raw_value.split(",")
        if item.strip()
    )


def model_download_candidates(url: str) -> list[str]:
    candidates: list[str] = []
    parsed = urlparse(url)
    filename = Path(parsed.path).name

    if parsed.netloc == "github.com" and filename in UPSTREAM_HUGGINGFACE_FILES:
        candidates.extend(
            [
                f"https://hf-mirror.com/zyddnys/manga-image-translator/resolve/main/{filename}",
                f"https://huggingface.co/zyddnys/manga-image-translator/resolve/main/{filename}",
            ]
        )
    if parsed.netloc == "huggingface.co":
        candidates.append(url.replace("https://huggingface.co/", "https://hf-mirror.com/", 1))
    if parsed.netloc == "github.com":
        candidates.extend(f"{prefix}{url}" for prefix in _github_mirror_prefixes())
    candidates.append(url)

    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return unique


def _download_once(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    downloaded_size = path.stat().st_size if path.exists() else 0
    headers = {"User-Agent": "Solar-Manga-Translator/model-downloader"}
    if downloaded_size:
        headers["Range"] = f"bytes={downloaded_size}-"
        headers["Accept-Encoding"] = "identity"

    response = requests.get(
        url,
        stream=True,
        allow_redirects=True,
        headers=headers,
        timeout=(
            _env_seconds("MT_MODEL_CONNECT_TIMEOUT", 10),
            _env_seconds("MT_MODEL_READ_TIMEOUT", 30),
        ),
    )
    try:
        if response.status_code == 416 and downloaded_size:
            return
        response.raise_for_status()

        append = bool(downloaded_size and response.status_code == 206)
        if not append:
            downloaded_size = 0
        total = int(response.headers.get("content-length", 0)) + downloaded_size
        chunk_size = 1024 * 1024
        with tqdm.tqdm(
            desc=path.name,
            initial=downloaded_size,
            total=total or None,
            unit="iB",
            unit_scale=True,
            unit_divisor=1024,
        ) as progress:
            with path.open("ab" if append else "wb") as handle:
                last_non_tty_report = downloaded_size
                for data in response.iter_content(chunk_size=chunk_size):
                    if not data:
                        continue
                    written = handle.write(data)
                    progress.update(written)
                    if (
                        not sys.stdout.isatty()
                        and progress.n - last_non_tty_report >= 32 * 1024 * 1024
                    ):
                        print(f"[ModelDownload] {path.name}: {progress.n}/{total or '?'} bytes")
                        last_non_tty_report = progress.n
    finally:
        response.close()


def _verify_known_model(path: Path, source_url: str) -> None:
    filename = Path(urlparse(source_url).path).name
    expected = EXPECTED_MODEL_SHA256.get(filename)
    if not expected:
        return
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual == expected:
        return
    path.unlink(missing_ok=True)
    raise ModelChecksumError(
        f"checksum mismatch for {filename}: expected {expected}, got {actual}"
    )


def download_url_with_progressbar(url: str, path: str) -> None:
    destination = Path(path)
    if destination.name in {".", ""} or destination.is_dir():
        filename = Path(urlparse(url).path).name
        if not filename:
            raise RuntimeError("Could not determine model filename")
        destination = destination / filename

    errors: list[str] = []
    for candidate in model_download_candidates(url):
        for attempt in range(1, 3):
            try:
                print(f'[ModelDownload] source="{candidate}" attempt={attempt}/2')
                _download_once(candidate, destination)
                _verify_known_model(destination, candidate)
                return
            except (OSError, requests.RequestException) as exc:
                errors.append(f"{candidate}: {exc}")
                print(f"[ModelDownload] source failed: {candidate} ({exc})")
                if attempt < 2:
                    time.sleep(attempt)

    summary = " | ".join(errors[-4:])
    raise RuntimeError(f"All model download sources failed for {destination.name}: {summary}")
