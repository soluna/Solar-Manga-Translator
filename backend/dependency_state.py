from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import platform
import sys
from pathlib import Path


BACKEND_FILES = (
    "backend/requirements.txt",
    "backend/requirements-upstream.txt",
    "backend/upstream.json",
    "backend/install_deps.py",
    "backend/pip_install.py",
    "backend/patch_pydensecrf.py",
    "backend/patched_model_download.py",
    "backend/patched_rerender_cache.py",
)
FRONTEND_FILES = (
    "frontend/package.json",
    "frontend/package-lock.json",
)
BACKEND_IMPORTS = ("fastapi", "uvicorn", "dotenv", "cv2", "numpy", "PIL")


def dependency_fingerprint(root: Path, scope: str) -> str:
    digest = hashlib.sha256()
    files = list(BACKEND_FILES if scope == "backend" else FRONTEND_FILES)
    if scope == "backend":
        files.extend(
            str(path.relative_to(root)).replace("\\", "/")
            for path in sorted((root / "backend").glob("patched_*.py"))
            if str(path.relative_to(root)).replace("\\", "/") not in files
        )
    digest.update(scope.encode("utf-8"))
    if scope == "backend":
        digest.update(
            f"{sys.implementation.name}:{sys.version_info.major}.{sys.version_info.minor}:{platform.system()}".encode(
                "utf-8"
            )
        )
    for relative_path in files:
        path = root / relative_path
        digest.update(relative_path.encode("utf-8"))
        digest.update(path.read_bytes() if path.exists() else b"[MISSING]")
    return digest.hexdigest()


def runtime_is_present(root: Path, scope: str) -> bool:
    if scope == "backend":
        upstream_package = root / "backend" / "manga-image-translator" / "manga_translator" / "__init__.py"
        return upstream_package.exists() and all(
            importlib.util.find_spec(module_name) is not None
            for module_name in BACKEND_IMPORTS
        )
    return (
        (root / "frontend" / "node_modules").is_dir()
        and (root / "frontend" / "node_modules" / "vite" / "package.json").exists()
    )


def stamp_matches(root: Path, scope: str, stamp_path: Path) -> bool:
    if not stamp_path.exists() or not runtime_is_present(root, scope):
        return False
    try:
        payload = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        payload.get("scope") == scope
        and payload.get("fingerprint") == dependency_fingerprint(root, scope)
    )


def write_stamp(root: Path, scope: str, stamp_path: Path) -> None:
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    stamp_path.write_text(
        json.dumps(
            {
                "scope": scope,
                "fingerprint": dependency_fingerprint(root, scope),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Solar-Manga-Translator dependency state.")
    parser.add_argument("action", choices=("check", "mark"))
    parser.add_argument("scope", choices=("backend", "frontend"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--stamp", type=Path, required=True)
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if args.action == "mark":
        write_stamp(root, args.scope, args.stamp)
        return 0
    return 0 if stamp_matches(root, args.scope, args.stamp) else 1


if __name__ == "__main__":
    raise SystemExit(main())
