"""Structured JSON logging with credential redaction.

All Insyte components log through the ``insyte`` logger. Every handler carries a redaction
filter that masks connection URLs and sensitive fields, so passwords, tokens and full
database URLs can never reach a log file or stderr.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

# Matches "scheme://user:password@" and lets us drop the password portion.
_URL_CREDENTIALS_RE = re.compile(
    r"(?P<scheme>[a-zA-Z][\w+.\-]*://)(?P<user>[^:@/\s]+):(?P<pw>[^@/\s]+)@"
)

_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "auth_token",
    "api_key",
    "apikey",
    "database_url",
    "db_url",
    "url",
    "dsn",
}

# Standard LogRecord attributes, so JSON output only adds genuine extras.
_RESERVED_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


def mask_url(value: str) -> str:
    """Return ``value`` with any embedded ``user:password@`` credentials masked."""

    return _URL_CREDENTIALS_RE.sub(lambda m: f"{m.group('scheme')}{m.group('user')}:***@", value)


class RedactionFilter(logging.Filter):
    """Masks credentials in log messages and any string/extra fields on the record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_url(record.msg)
        if record.args:
            record.args = tuple(
                mask_url(a) if isinstance(a, str) else a for a in _iter_args(record.args)
            )
        for key, value in list(record.__dict__.items()):
            if key in _RESERVED_ATTRS:
                continue
            if key.lower() in _SENSITIVE_KEYS:
                record.__dict__[key] = "***"
            elif isinstance(value, str):
                record.__dict__[key] = mask_url(value)
        return True


def _iter_args(args: Any) -> tuple[Any, ...]:
    if isinstance(args, tuple):
        return args
    return (args,)


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(
    *,
    level: int = logging.INFO,
    log_file: Path | None = None,
    force: bool = False,
) -> logging.Logger:
    """Configure the root ``insyte`` logger with JSON output and redaction.

    Idempotent unless ``force`` is set. When ``log_file`` is provided, logs are written there
    in addition to stderr.
    """

    root = logging.getLogger("insyte")
    if root.handlers and not force:
        return root

    root.handlers.clear()
    root.setLevel(level)
    root.propagate = False

    formatter = JsonFormatter()
    redaction = RedactionFilter()

    stderr_handler = logging.StreamHandler()
    stderr_handler.setFormatter(formatter)
    stderr_handler.addFilter(redaction)
    root.addHandler(stderr_handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redaction)
        root.addHandler(file_handler)

    return root


def get_logger(name: str) -> logging.Logger:
    """Return a child of the ``insyte`` logger."""

    return logging.getLogger(f"insyte.{name}")
