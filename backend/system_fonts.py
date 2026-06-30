from __future__ import annotations

import filecmp
import os
import shutil
from pathlib import Path


FONT_EXTENSIONS = {".ttf", ".ttc", ".otf"}
BUNDLED_DEFAULT_FONT_NAME = "SourceHanSansSC-Regular-2.otf"
BUNDLED_PREFERRED_FONT_NAMES = (
    BUNDLED_DEFAULT_FONT_NAME,
    "SourceHanSansSC-Medium-2.otf",
    "SourceHanSansSC-Bold.otf",
)


def font_root_directory(code_dir: Path) -> Path:
    override = str(os.getenv("APP_FONT_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path(code_dir).resolve().parent / "fonts").resolve()


def bundled_font_directories(code_dir: Path) -> tuple[Path, ...]:
    return (font_root_directory(code_dir) / "system",)


def custom_font_directories(code_dir: Path) -> tuple[Path, ...]:
    return (font_root_directory(code_dir) / "custom",)


def ensure_project_font_directories(code_dir: Path) -> Path:
    root = font_root_directory(code_dir)
    system_dir = root / "system"
    custom_dir = root / "custom"
    system_dir.mkdir(parents=True, exist_ok=True)
    custom_dir.mkdir(parents=True, exist_ok=True)
    migrate_legacy_flat_fonts(root, system_dir=system_dir, custom_dir=custom_dir)
    return root


def migrate_legacy_flat_fonts(root: Path, *, system_dir: Path, custom_dir: Path) -> None:
    try:
        legacy_fonts = [
            path
            for path in root.iterdir()
            if path.is_file() and path.suffix.lower() in FONT_EXTENSIONS
        ]
    except OSError:
        return

    for source in legacy_fonts:
        bundled_copy = system_dir / source.name
        try:
            if bundled_copy.is_file() and filecmp.cmp(source, bundled_copy, shallow=False):
                source.unlink()
                continue
        except OSError:
            pass

        target = custom_dir / source.name
        if target.exists():
            try:
                if target.is_file() and filecmp.cmp(source, target, shallow=False):
                    source.unlink()
                    continue
            except OSError:
                pass
            index = 1
            while target.exists():
                target = custom_dir / f"{source.stem}-legacy-{index}{source.suffix}"
                index += 1
        try:
            shutil.move(str(source), str(target))
        except OSError:
            continue


def find_default_bundled_font(code_dir: Path) -> Path | None:
    directories = bundled_font_directories(code_dir)
    for name in BUNDLED_PREFERRED_FONT_NAMES:
        for directory in directories:
            candidate = directory / name
            if candidate.is_file():
                return candidate.resolve()

    for directory in directories:
        try:
            fallback = next(
                (
                    path
                    for path in directory.rglob("*")
                    if path.is_file() and path.suffix.lower() in FONT_EXTENSIONS
                ),
                None,
            )
        except OSError:
            fallback = None
        if fallback is not None:
            return fallback.resolve()
    return None
