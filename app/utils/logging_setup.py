"""Logging configuration.

Debug logs are written to a rotating file so a crash or an API failure can be
diagnosed later. A redaction filter makes sure API keys never reach the log,
even if a library tries to log the full request.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path

__all__ = ["setup_logging", "RedactSecretsFilter", "get_logger"]

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(?i)(api[_-]?key\"?\s*[:=]\s*\"?)([^\s\"',}]+)"),
    re.compile(r"(?i)(authorization\"?\s*[:=]\s*\"?bearer\s+)([^\s\"',}]+)"),
)


class RedactSecretsFilter(logging.Filter):
    """Replace anything that looks like an API key with ``***``."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        redacted = message
        for pattern in _SECRET_PATTERNS:
            if pattern.groups >= 2:
                redacted = pattern.sub(lambda m: f"{m.group(1)}***", redacted)
            else:
                redacted = pattern.sub("***", redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def setup_logging(log_directory: Path | None, *, debug: bool = False) -> logging.Logger:
    """Configure the root logger and return the application logger."""
    logger = logging.getLogger("nutrition")
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    redaction = RedactSecretsFilter()

    if log_directory is not None:
        try:
            log_directory.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_directory / "app.log",
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler.addFilter(redaction)
            logger.addHandler(file_handler)
        except OSError:
            # A read-only log folder must never stop the app from starting.
            pass

    # 打包成 --windowed 的 EXE 时，sys.stderr 可能是 None，此时不添加控制台 handler。
    if sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        stream_handler.addFilter(redaction)
        logger.addHandler(stream_handler)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child of the application logger."""
    if not name:
        return logging.getLogger("nutrition")
    return logging.getLogger(f"nutrition.{name}")
