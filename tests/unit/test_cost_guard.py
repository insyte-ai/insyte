"""Unit tests for the cost guard (limit injection + cartesian detection)."""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from insyte.query.cost_guard import apply_row_limit, max_cartesian_width


def _parse(sql: str) -> exp.Query:
    parsed = sqlglot.parse_one(sql, read="postgres")
    assert isinstance(parsed, exp.Query)
    return parsed


def test_apply_limit_when_missing() -> None:
    expr, applied = apply_row_limit(_parse("SELECT 1"), 500, 5000)
    assert applied == 500
    assert expr.sql().endswith("LIMIT 500")


def test_apply_limit_keeps_smaller() -> None:
    _, applied = apply_row_limit(_parse("SELECT 1 LIMIT 25"), 500, 5000)
    assert applied == 25


def test_apply_limit_clamps_larger() -> None:
    _, applied = apply_row_limit(_parse("SELECT 1 LIMIT 100000"), 500, 5000)
    assert applied == 5000


def test_cartesian_width_join_with_condition_is_not_cartesian() -> None:
    assert max_cartesian_width(_parse("SELECT * FROM a JOIN b ON a.id = b.id")) == 0


def test_cartesian_width_explicit_cross() -> None:
    assert max_cartesian_width(_parse("SELECT * FROM a CROSS JOIN b")) == 2


def test_cartesian_width_comma_join() -> None:
    assert max_cartesian_width(_parse("SELECT * FROM a, b, c")) == 3
