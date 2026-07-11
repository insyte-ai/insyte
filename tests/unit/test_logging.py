"""Tests for structured JSON logging and credential redaction."""

from __future__ import annotations

import json
import logging

from insyte.logging_config import JsonFormatter, RedactionFilter, mask_url


def _record(msg: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("insyte.test", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_mask_url_hides_password() -> None:
    masked = mask_url("postgresql://reader:s3cret@host:5432/db")
    assert "s3cret" not in masked
    assert "reader" in masked
    assert ":***@" in masked


def test_redaction_filter_masks_message() -> None:
    record = _record("connecting to postgresql://reader:s3cret@host/db")
    RedactionFilter().filter(record)
    assert "s3cret" not in record.getMessage()


def test_redaction_filter_masks_sensitive_extras() -> None:
    record = _record("hi", password="hunter2", token="abc123")
    RedactionFilter().filter(record)
    assert record.__dict__["password"] == "***"
    assert record.__dict__["token"] == "***"


def test_json_formatter_produces_valid_json() -> None:
    record = _record("hello", project="demo")
    formatted = JsonFormatter().format(record)
    payload = json.loads(formatted)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "insyte.test"
    assert payload["message"] == "hello"
    assert payload["project"] == "demo"
    assert "timestamp" in payload


def test_json_formatter_after_redaction_has_no_secret() -> None:
    record = _record("dsn=postgresql://reader:s3cret@host/db", api_key="topsecret")
    RedactionFilter().filter(record)
    payload = json.loads(JsonFormatter().format(record))
    assert "s3cret" not in json.dumps(payload)
    assert payload["api_key"] == "***"
