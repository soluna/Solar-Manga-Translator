from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def configure_rotating_file_logging(
    log_path: Path,
    *,
    logger: logging.Logger | None = None,
    level: int = logging.INFO,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    include_stream: bool = True,
) -> logging.Handler:
    target_logger = logger or logging.getLogger()
    target_logger.setLevel(level)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    normalized_path = str(log_path.resolve())

    existing = next(
        (
            handler
            for handler in target_logger.handlers
            if isinstance(handler, RotatingFileHandler)
            and str(Path(getattr(handler, "baseFilename", "")).resolve()) == normalized_path
        ),
        None,
    )
    formatter = logging.Formatter(LOG_FORMAT)
    if existing is None:
        existing = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        existing.setFormatter(formatter)
        target_logger.addHandler(existing)

    has_stream = any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, logging.FileHandler)
        for handler in target_logger.handlers
    )
    if include_stream and not has_stream:
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        target_logger.addHandler(stream_handler)
    return existing
