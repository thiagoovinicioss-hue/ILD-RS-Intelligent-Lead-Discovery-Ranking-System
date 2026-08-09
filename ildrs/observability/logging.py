"""Structured logging.

Provides a JSON formatter (machine-readable) and a concise text formatter
(human-friendly) driven by ``ILD_LOG_LEVEL`` / ``ILD_LOG_JSON``.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception"] = self.formatException(record.exc_info)
        extras = getattr(record, "extra_fields", None)
        if extras:
            payload.update(extras)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    LEVEL_COLORS = {
        "DEBUG": "\033[90m",  # gray
        "INFO": "\033[36m",  # cyan
        "WARNING": "\033[33m",  # yellow
        "ERROR": "\033[31m",  # red
        "CRITICAL": "\033[1;31m",  # bold red
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelname, "")
        level = f"{color}{record.levelname:<8}{self.RESET}"
        ts = datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S")
        message = record.getMessage()
        extras = getattr(record, "extra_fields", None)
        suffix = f"  {json.dumps(extras, default=str)}" if extras else ""
        return f"{ts} {level} {record.name}: {message}{suffix}"


def configure_logging(level: str = "", json_mode: bool | None = None) -> None:
    from ildrs.config import get_settings

    settings = get_settings()
    log_level = (level or settings.log_level).upper()
    use_json = settings.log_json if json_mode is None else json_mode

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if use_json else TextFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(log_level)
    # keep httpx/uvicorn noisy logs quiet unless debugging
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def log_with_extras(logger: logging.Logger, level: int, message: str, **extras: Any) -> None:
    logger.log(level, message, extra={"extra_fields": extras})


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
