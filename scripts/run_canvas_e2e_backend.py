#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
for path in (ROOT, BACKEND_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backend.tests._textblock_stub import textblock_module_patch  # noqa: E402


TEXTBLOCK_PATCHER = textblock_module_patch()
TEXTBLOCK_PATCHER.start()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the canvas E2E backend without the optional inference runtime."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run("main:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
