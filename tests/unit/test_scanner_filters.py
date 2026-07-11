"""Unit tests for the scanner's pure privacy filters."""

from __future__ import annotations

from insyte.config.models import DatabaseSection
from insyte.metadata.scanner import (
    blocked_column_names,
    blocked_table_names,
    is_column_blocked,
    is_table_blocked,
)


def test_blocked_column_accepts_two_and_three_part() -> None:
    db = DatabaseSection(blocked_columns=["users.password_hash", "public.users.auth_token"])
    keys = blocked_column_names(db)
    assert "users.password_hash" in keys
    assert "users.auth_token" in keys  # schema stripped to table.column
    assert is_column_blocked("users", "password_hash", keys)
    assert is_column_blocked("USERS", "Auth_Token", keys)  # case-insensitive
    assert not is_column_blocked("users", "email", keys)


def test_blocked_table_qualified_and_bare() -> None:
    db = DatabaseSection(blocked_tables=["secrets", "public.audit_log"])
    blocked = blocked_table_names(db)
    assert is_table_blocked("public", "secrets", blocked)
    assert is_table_blocked("public", "audit_log", blocked)
    assert not is_table_blocked("public", "orders", blocked)
