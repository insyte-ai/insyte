"""Detect and mask possible PII in columns (spec §11).

Detection combines column-name patterns, data types, and regexes over *sampled* values. Values
that may be PII are masked before they are ever stored in local metadata or shown — and are
never sent to an AI provider.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class PiiType(StrEnum):
    email = "email"
    phone = "phone"
    credit_card = "credit_card"
    ssn = "ssn"
    ip_address = "ip_address"
    name = "name"
    address = "address"
    date_of_birth = "date_of_birth"
    secret = "secret"
    other = "other"


@dataclass
class PiiClassification:
    is_pii: bool
    pii_type: PiiType | None
    confidence: float
    method: str  # name | regex | disabled | none


# Column-name substrings → PII type (checked first; cheapest and most reliable).
_NAME_PATTERNS: dict[PiiType, tuple[str, ...]] = {
    PiiType.email: ("email", "e_mail"),
    PiiType.secret: ("password", "passwd", "pwd", "secret", "token", "auth", "api_key", "apikey"),
    PiiType.ssn: ("ssn", "social_security", "aadhaar", "aadhar", "national_id", "pan_number"),
    PiiType.credit_card: ("credit_card", "card_number", "cc_number", "ccnum"),
    PiiType.date_of_birth: ("date_of_birth", "birth_date", "birthdate", "dob"),
    PiiType.ip_address: ("ip_address", "ip_addr", "client_ip"),
    PiiType.phone: ("phone", "mobile", "msisdn", "contact_number"),
    PiiType.name: ("first_name", "last_name", "full_name", "fname", "lname"),
    PiiType.address: ("address", "street", "postal_code", "postcode", "zipcode", "pincode"),
}

# Regexes applied to sampled string values (text columns only).
_VALUE_PATTERNS: dict[PiiType, re.Pattern[str]] = {
    PiiType.email: re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$"),
    PiiType.ip_address: re.compile(r"^\d{1,3}(\.\d{1,3}){3}$"),
    PiiType.ssn: re.compile(r"^\d{3}-\d{2}-\d{4}$"),
    PiiType.credit_card: re.compile(r"^\d[\d -]{13,17}\d$"),
    PiiType.phone: re.compile(r"^\+?\d[\d\s().-]{6,}\d$"),
}

_TEXT_TYPES = ("char", "text", "citext", "varchar", "character")
_REGEX_MATCH_THRESHOLD = 0.7


def _is_text(data_type: str) -> bool:
    lowered = data_type.lower()
    return any(t in lowered for t in _TEXT_TYPES)


def classify_column(
    name: str,
    data_type: str,
    sample_values: list[object],
    *,
    detect_pii: bool = True,
) -> PiiClassification:
    """Classify whether a column likely holds PII."""

    if not detect_pii:
        return PiiClassification(False, None, 0.0, "disabled")

    lowered = name.lower()
    for pii_type, keywords in _NAME_PATTERNS.items():
        if any(keyword in lowered for keyword in keywords):
            return PiiClassification(True, pii_type, 0.9, "name")

    if _is_text(data_type):
        strings = [str(v) for v in sample_values if v is not None][:200]
        if strings:
            for pii_type, pattern in _VALUE_PATTERNS.items():
                matches = sum(1 for value in strings if pattern.match(value))
                ratio = matches / len(strings)
                if ratio >= _REGEX_MATCH_THRESHOLD:
                    return PiiClassification(True, pii_type, round(ratio, 2), "regex")

    return PiiClassification(False, None, 0.0, "none")


def mask_value(value: object) -> str:
    """Return a masked representation that never reveals the full value."""

    if value is None:
        return ""
    text = str(value)
    if len(text) <= 1:
        return "*"
    if len(text) <= 4:
        return f"{text[0]}***"
    return f"{text[0]}***{text[-1]}"
