from __future__ import annotations

import os
import stat
import warnings
import zipfile
from contextlib import suppress
from pathlib import Path, PurePosixPath, PureWindowsPath


IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
DEFAULT_MAX_ARCHIVE_MEMBERS = 5000
DEFAULT_MAX_IMAGE_FILES = 2000
DEFAULT_MAX_MEMBER_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO = 200
DEFAULT_MAX_IMAGE_PIXELS = 100_000_000


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _safe_member_path(extract_root: Path, member_name: str) -> Path:
    normalized_name = member_name.replace("\\", "/")
    posix_path = PurePosixPath(normalized_name)
    windows_path = PureWindowsPath(member_name)
    if posix_path.is_absolute() or windows_path.is_absolute():
        raise ValueError("压缩包包含不安全的绝对路径。")
    if not posix_path.name:
        raise ValueError("压缩包包含无效文件名。")
    if any(part in {"", ".", ".."} for part in posix_path.parts):
        raise ValueError("压缩包包含不安全的相对路径。")
    if posix_path.parts and ":" in posix_path.parts[0]:
        raise ValueError("压缩包包含不安全的驱动器路径。")

    target_path = (extract_root / Path(*posix_path.parts)).resolve()
    resolved_root = extract_root.resolve()
    if target_path != resolved_root and resolved_root not in target_path.parents:
        raise ValueError("压缩包文件试图写出解压目录。")
    return target_path


def _is_regular_zip_file(member: zipfile.ZipInfo) -> bool:
    mode = member.external_attr >> 16
    if not mode:
        return True
    file_type = stat.S_IFMT(mode)
    return file_type in {0, stat.S_IFREG}


def verify_supported_image(path: Path) -> bool:
    try:
        from PIL import Image

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                width, height = image.size
                if width <= 0 or height <= 0:
                    raise ValueError("图片尺寸无效。")
                max_pixels = _env_int("MT_MAX_IMAGE_PIXELS", DEFAULT_MAX_IMAGE_PIXELS)
                if width * height > max_pixels:
                    raise ValueError("图片像素数量过大。")
                image.verify()
        return True
    except Exception:
        with suppress(FileNotFoundError):
            path.unlink()
        return False


def _copy_member(
    zip_ref: zipfile.ZipFile,
    member: zipfile.ZipInfo,
    target_path: Path,
    max_member_bytes: int,
) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    copied = 0
    try:
        with zip_ref.open(member, "r") as source, target_path.open("wb") as destination:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > max_member_bytes:
                    raise ValueError("压缩包内单个图片过大。")
                destination.write(chunk)
    except Exception:
        with suppress(FileNotFoundError):
            target_path.unlink()
        raise


def extract_archive(file_path: str, extract_dir: str) -> list[str]:
    archive_path = Path(file_path)
    extract_root = Path(extract_dir).resolve()
    extract_root.mkdir(parents=True, exist_ok=True)

    if not zipfile.is_zipfile(archive_path):
        raise ValueError("上传的压缩包格式无效。")

    max_members = _env_int("MT_MAX_ARCHIVE_MEMBERS", DEFAULT_MAX_ARCHIVE_MEMBERS)
    max_image_files = _env_int("MT_MAX_IMAGE_FILES", DEFAULT_MAX_IMAGE_FILES)
    max_member_bytes = _env_int("MT_MAX_ARCHIVE_MEMBER_BYTES", DEFAULT_MAX_MEMBER_BYTES)
    max_total_bytes = _env_int("MT_MAX_ARCHIVE_TOTAL_BYTES", DEFAULT_MAX_TOTAL_BYTES)
    max_compression_ratio = _env_int(
        "MT_MAX_ARCHIVE_COMPRESSION_RATIO",
        DEFAULT_MAX_COMPRESSION_RATIO,
    )

    images: list[str] = []
    total_uncompressed = 0

    with zipfile.ZipFile(archive_path, "r") as zip_ref:
        members = zip_ref.infolist()
        if len(members) > max_members:
            raise ValueError("压缩包内文件数量过多。")

        for member in members:
            if member.is_dir():
                continue
            if not _is_regular_zip_file(member):
                raise ValueError("压缩包包含不支持的特殊文件。")

            target_path = _safe_member_path(extract_root, member.filename)
            if target_path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue

            if member.file_size > max_member_bytes:
                raise ValueError("压缩包内单个图片过大。")
            if member.compress_size <= 0 and member.file_size > 0:
                raise ValueError("压缩包包含异常压缩条目。")
            if member.compress_size > 0 and member.file_size / member.compress_size > max_compression_ratio:
                raise ValueError("压缩包压缩比异常，可能不是安全的图片包。")

            total_uncompressed += member.file_size
            if total_uncompressed > max_total_bytes:
                raise ValueError("压缩包解压后总大小过大。")
            if len(images) >= max_image_files:
                raise ValueError("压缩包内图片数量过多。")

            _copy_member(zip_ref, member, target_path, max_member_bytes)
            if verify_supported_image(target_path):
                images.append(str(target_path))

    return sorted(images)
