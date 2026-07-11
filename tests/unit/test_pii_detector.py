"""Unit tests for PII detection and masking."""

from __future__ import annotations

from insyte.metadata.pii_detector import PiiType, classify_column, mask_value


def test_detect_by_name_email() -> None:
    result = classify_column("email", "text", ["x@y.com"])
    assert result.is_pii and result.pii_type is PiiType.email and result.method == "name"


def test_detect_by_name_password() -> None:
    result = classify_column("password_hash", "text", ["abc"])
    assert result.is_pii and result.pii_type is PiiType.secret


def test_detect_by_regex_email_values() -> None:
    result = classify_column("contact", "varchar", ["a@b.com", "c@d.org", "e@f.net"])
    assert result.is_pii and result.pii_type is PiiType.email and result.method == "regex"


def test_detect_by_regex_ip() -> None:
    result = classify_column("host", "text", ["10.0.0.1", "192.168.1.5", "8.8.8.8"])
    assert result.is_pii and result.pii_type is PiiType.ip_address


def test_numeric_ids_not_flagged_by_regex() -> None:
    # Integer columns are not regex-scanned, so ordinary ids are not mistaken for phones.
    result = classify_column("order_id", "integer", [1234567, 2345678, 3456789])
    assert result.is_pii is False


def test_non_pii_category() -> None:
    result = classify_column("status", "text", ["completed", "pending", "completed"])
    assert result.is_pii is False


def test_detection_disabled() -> None:
    result = classify_column("email", "text", ["x@y.com"], detect_pii=False)
    assert result.is_pii is False and result.method == "disabled"


def test_mask_value() -> None:
    assert mask_value("alice@example.com") == "a***m"
    assert mask_value("ab") == "a***"
    assert mask_value("x") == "*"
    assert mask_value(None) == ""
    # A masked value never contains the original in full.
    assert "example" not in mask_value("alice@example.com")
