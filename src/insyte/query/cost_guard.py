"""Cost controls: cartesian-join detection and automatic row limiting.

These operate on the parsed SQLGlot AST, never on the raw string.
"""

from __future__ import annotations

from sqlglot import exp

# A cartesian product across this many or more tables is rejected as excessive.
DEFAULT_MAX_CARTESIAN_TABLES = 2


def _is_cartesian_join(join: exp.Join) -> bool:
    """True if a join has no ON/USING condition (an explicit CROSS or an implicit comma join)."""

    if join.args.get("on") is not None or join.args.get("using"):
        return False
    kind = (join.args.get("kind") or "").upper()
    side = (join.args.get("side") or "").upper()
    method = (join.args.get("method") or "").upper()
    if method == "NATURAL" or side in {"LEFT", "RIGHT", "FULL"}:
        return False
    return kind in {"", "CROSS", "INNER"}


def max_cartesian_width(expression: exp.Expression) -> int:
    """Return the largest number of tables joined without a condition, across all selects."""

    widest = 0
    for select in expression.find_all(exp.Select):
        joins = select.args.get("joins") or []
        cartesian = sum(1 for j in joins if _is_cartesian_join(j))
        if cartesian:
            widest = max(widest, cartesian + 1)  # +1 for the base table
    return widest


def _limit_value(expression: exp.Query) -> int | None:
    limit = expression.args.get("limit")
    if limit is None:
        return None
    value = limit.args.get("expression") if isinstance(limit, exp.Limit) else None
    if isinstance(value, exp.Literal) and value.is_number:
        return int(value.name)
    return None  # LIMIT ALL, expression-based, or unparseable → treat as "no safe bound"


def apply_row_limit(
    expression: exp.Query, default_limit: int, maximum_limit: int
) -> tuple[exp.Query, int]:
    """Ensure the query has a bounded LIMIT, returning the (possibly modified) AST and limit.

    * No limit → add ``default_limit``.
    * Limit above the maximum (or unbounded ``LIMIT ALL``) → clamp to ``maximum_limit``.
    * Otherwise keep the query's own limit.
    """

    current = _limit_value(expression)
    if current is None:
        existing = expression.args.get("limit")
        target = maximum_limit if existing is not None else default_limit
        return expression.limit(target), target
    if current > maximum_limit:
        return expression.limit(maximum_limit), maximum_limit
    return expression, current
