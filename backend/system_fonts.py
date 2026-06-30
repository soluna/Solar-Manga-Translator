from __future__ import annotations

from pathlib import Path


FONT_EXTENSIONS = {".ttf", ".ttc", ".otf"}
BUNDLED_DEFAULT_FONT_NAME = "SourceHanSansSC-Regular-2.otf"
BUNDLED_PREFERRED_FONT_NAMES = (
    BUNDLED_DEFAULT_FONT_NAME,
    "SourceHanSansSC-Medium-2.otf",
    "SourceHanSansSC-Bold.otf",
)


def bundled_font_directories(code_dir: Path) -> tuple[Path, ...]:
    candidates = (
        Path(code_dir).resolve() / "typefaces",
    )
    return tuple(path for path in candidates if path.exists() and path.is_dir())


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
