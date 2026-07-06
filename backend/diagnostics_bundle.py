from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any


MAX_LOG_BYTES_PER_FILE = 2 * 1024 * 1024
SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "api_token",
    "apitoken",
    "authorization",
    "password",
    "secret",
    "token",
}
PATH_KEY_SUFFIXES = ("_dir", "_path", "_root", "_file")
OCR_LINE_PATTERNS = (
    re.compile(r"(?i)\b(?:model48pxocr|ocr text|recognized text|source text)\b"),
    re.compile(r"(?i)\b(?:gpt response|translated text|translation result)\b"),
)


def _looks_like_local_path(value: str) -> bool:
    normalized = str(value or "").strip()
    return bool(
        re.match(r"^[A-Za-z]:[\\/]", normalized)
        or normalized.startswith(("/Users/", "/Volumes/", "/home/", "/private/", "/tmp/"))
    )


def _redact(value: Any, key: str = "") -> Any:
    normalized_key = str(key or "").lower()
    is_sensitive = (
        normalized_key in SENSITIVE_KEYS
        or normalized_key.endswith(("_api_key", "_password", "_secret", "_token"))
    )
    if is_sensitive:
        return "[REDACTED]" if value else value
    if isinstance(value, str) and (
        normalized_key.endswith(PATH_KEY_SUFFIXES)
        or _looks_like_local_path(value)
    ):
        return "[LOCAL_PATH]" if value else value
    if isinstance(value, dict):
        return {
            str(child_key): _redact(child_value, str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _read_log_tail_bytes(path: Path) -> bytes:
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - MAX_LOG_BYTES_PER_FILE))
        content = handle.read().decode("utf-8", errors="replace")
    content = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+",
        r"\1[REDACTED]",
        content,
    )
    content = re.sub(
        r'(?i)(["\']?(?:api[_-]?key|token|password|secret)["\']?\s*[:=]\s*["\']?)[^"\'\s,}]+',
        r"\1[REDACTED]",
        content,
    )
    sanitized_lines: list[str] = []
    for line in content.splitlines():
        if any(pattern.search(line) for pattern in OCR_LINE_PATTERNS):
            sanitized_lines.append("[CONTENT REDACTED]")
            continue
        line = re.sub(
            r"(?i)[A-Za-z]:\\(?:[^\\\r\n\t :]+\\)*[^\\\r\n\t :]*",
            "[LOCAL_PATH]",
            line,
        )
        line = re.sub(
            r"(?<!https:)(?<!http:)(?:/Users|/Volumes|/home|/private|/tmp)/[^\s\"']+",
            "[LOCAL_PATH]",
            line,
        )
        sanitized_lines.append(line)
    content = "\n".join(sanitized_lines)
    return content.encode("utf-8")


def build_diagnostics_zip(
    *,
    diagnostics: dict[str, Any],
    runtime: dict[str, Any],
    settings: dict[str, Any],
    logs_dir: Path,
) -> bytes:
    summary = {
        "diagnostics": _redact(diagnostics),
        "runtime": _redact(runtime),
        "settings": _redact(settings),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "diagnostics.json",
            json.dumps(summary, ensure_ascii=False, indent=2),
        )
        if logs_dir.exists():
            for path in sorted(logs_dir.rglob("*.log*")):
                if not path.is_file():
                    continue
                relative_path = path.relative_to(logs_dir)
                archive.writestr(
                    f"logs/{str(relative_path).replace(chr(92), '/')}",
                    _read_log_tail_bytes(path),
                )
    return output.getvalue()
