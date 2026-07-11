"""Unit tests for the query validator's behaviour on valid queries and edge cases."""

from __future__ import annotations

import pytest

from insyte.config.models import DatabaseSection, InsyteConfig, ProjectSection, QuerySection
from insyte.query.validator import ValidationContext, validate_query


def _context(**db_kwargs: object) -> ValidationContext:
    config = InsyteConfig(
        project=ProjectSection(name="t"),
        database=DatabaseSection(allowed_schemas=["public", "sales"], **db_kwargs),  # type: ignore[arg-type]
        query=QuerySection(default_limit=500, maximum_limit=5000),
    )
    return ValidationContext.from_config(config)


def test_limit_added_when_missing() -> None:
    result = validate_query("SELECT id FROM orders", _context())
    assert result.valid
    assert result.applied_limit == 500
    assert result.normalized_sql is not None and result.normalized_sql.endswith("LIMIT 500")


def test_limit_preserved_when_reasonable() -> None:
    result = validate_query("SELECT id FROM orders LIMIT 10", _context())
    assert result.applied_limit == 10


def test_limit_clamped_to_maximum() -> None:
    result = validate_query("SELECT id FROM orders LIMIT 99999", _context())
    assert result.applied_limit == 5000


def test_referenced_tables_and_columns() -> None:
    result = validate_query(
        "SELECT o.id, c.name FROM public.orders o JOIN customers c ON o.customer_id = c.id",
        _context(),
    )
    assert "public.orders" in result.referenced_tables
    assert "customers" in result.referenced_tables
    assert "o.id" in result.referenced_columns


def test_allowed_schema_accepted() -> None:
    assert validate_query("SELECT id FROM sales.leads", _context()).valid


def test_disallowed_schema_rejected() -> None:
    result = validate_query("SELECT id FROM secret.vault", _context())
    assert not result.valid
    assert any("secret" in v for v in result.violations)


def test_blocked_table_rejected() -> None:
    result = validate_query("SELECT id FROM audit_log", _context(blocked_tables=["audit_log"]))
    assert not result.valid


def test_qualified_blocked_column_rejected() -> None:
    ctx = _context(blocked_columns=["users.password_hash"])
    result = validate_query("SELECT users.password_hash FROM users", ctx)
    assert not result.valid


def test_single_cross_join_allowed() -> None:
    # One cross join (two tables) is under the limit of two.
    result = validate_query("SELECT * FROM a CROSS JOIN b", _context())
    assert result.valid


def test_two_cross_joins_rejected() -> None:
    result = validate_query("SELECT 1 FROM a CROSS JOIN b CROSS JOIN c", _context())
    assert not result.valid
    assert any("cross join" in v.lower() for v in result.violations)


def test_union_is_allowed() -> None:
    result = validate_query("SELECT id FROM orders UNION SELECT id FROM customers", _context())
    assert result.valid


def test_empty_query_rejected() -> None:
    assert not validate_query("", _context()).valid


@pytest.mark.parametrize("sql", ["VACUUM", "SET search_path TO public", "EXPLAIN ANALYZE SELECT 1"])
def test_non_select_commands_rejected(sql: str) -> None:
    assert not validate_query(sql, _context()).valid
