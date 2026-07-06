from __future__ import annotations

import os
import subprocess
import sys


DEFAULT_INDEXES = (
    "https://mirrors.aliyun.com/pypi/simple/",
    "https://pypi.org/simple/",
)


def pip_indexes() -> list[str]:
    configured = os.getenv("MT_PIP_INDEXES", "").strip()
    if configured:
        raw_indexes = configured.split(",")
    else:
        raw_indexes = [
            os.getenv("PIP_INDEX_URL", "").strip(),
            *DEFAULT_INDEXES,
        ]
    indexes: list[str] = []
    for raw_index in raw_indexes:
        index = raw_index.strip()
        if index and index not in indexes:
            indexes.append(index)
    return indexes


def install_with_fallback(arguments: list[str]) -> None:
    errors: list[str] = []
    indexes = pip_indexes()
    for index, source in enumerate(indexes, start=1):
        print(f"[PyPI] 下载源 {index}/{len(indexes)}: {source}")
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--timeout",
            "30",
            "--index-url",
            source,
            *arguments,
        ]
        try:
            subprocess.run(command, check=True)
            return
        except subprocess.CalledProcessError as exc:
            errors.append(f"{source}: exit {exc.returncode}")
            print(f"[PyPI] 当前下载源失败，正在切换：{source}")
    raise RuntimeError("所有 PyPI 下载源均失败：" + " | ".join(errors))


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        raise SystemExit("Usage: pip_install.py <pip install arguments>")
    install_with_fallback(arguments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
