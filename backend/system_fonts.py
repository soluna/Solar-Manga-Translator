from __future__ import annotations

import os
import sys
from pathlib import Path


FONT_EXTENSIONS = {".ttf", ".ttc", ".otf"}


def system_font_directories() -> tuple[Path, ...]:
    home = Path.home()
    if os.name == "nt":
        windows_dir = Path(os.getenv("WINDIR") or "C:/Windows")
        candidates = (windows_dir / "Fonts",)
    elif sys.platform == "darwin":
        candidates = (
            Path("/System/Library/Fonts"),
            Path("/Library/Fonts"),
            home / "Library" / "Fonts",
        )
    else:
        candidates = (
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            home / ".local" / "share" / "fonts",
            home / ".fonts",
        )

    return tuple(path for path in candidates if path.exists() and path.is_dir())


def find_default_system_font() -> Path | None:
    preferred_names = (
        "msyh.ttc",
        "msyhbd.ttc",
        "msgothic.ttc",
        "YuGothM.ttc",
        "PingFang.ttc",
        "Hiragino Sans GB.ttc",
        "NotoSansCJK-Regular.ttc",
        "NotoSansCJKsc-Regular.otf",
        "NotoSansSC-Regular.otf",
        "DejaVuSans.ttf",
    )
    directories = system_font_directories()

    for name in preferred_names:
        for directory in directories:
            direct = directory / name
            if direct.is_file():
                return direct.resolve()
            try:
                nested = next(
                    (
                        path
                        for path in directory.rglob(name)
                        if path.is_file() and path.suffix.lower() in FONT_EXTENSIONS
                    ),
                    None,
                )
            except OSError:
                nested = None
            if nested is not None:
                return nested.resolve()

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
