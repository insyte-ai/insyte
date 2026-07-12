"""Generate safe analytical SQL from semantic metrics and dimensions.

SQL is built with the SQLGlot expression builder (not string concatenation), so literals are
quoted correctly. The output still passes through the Milestone 4 validator/executor before it
reaches the database — this module only assembles well-formed SELECTs.

Segmentation joins a metric's source table to a dimension's table using the relationships
discovered during ``insyte scan`` (a BFS over the relationship graph).
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import cast

import sqlglot
from sqlglot import exp

from insyte.exceptions import AnalysisError, JoinPathError
from insyte.metadata.models import RelationshipInfo
from insyte.semantic.models import Dimension, Metric

_DIALECT = "postgres"


def _literal(value: str | int | float) -> exp.Expression:
    if isinstance(value, bool):
        return cast(exp.Expression, exp.convert(value))
    if isinstance(value, int | float):
        return exp.Literal.number(value)
    return exp.Literal.string(str(value))


def _column(qualified: str) -> exp.Column:
    parts = qualified.split(".")
    table = parts[-2] if len(parts) >= 2 else None
    return exp.column(parts[-1], table=table)


def _filter_condition(column: str, values: list[str | int | float]) -> exp.Expression:
    col = _column(column)
    literals = [_literal(v) for v in values]
    if len(literals) == 1:
        return exp.EQ(this=col, expression=literals[0])
    return exp.In(this=col, expressions=literals)


def _apply_filters(select: exp.Select, metric: Metric) -> exp.Select:
    for column, values in metric.filters.items():
        if values:
            select = select.where(_filter_condition(column, values))
    return select


def _apply_period(
    select: exp.Select,
    time_column: str | None,
    start: datetime | None,
    end: datetime | None,
) -> exp.Select:
    if start is None and end is None:
        return select
    if time_column is None:
        raise AnalysisError("A period was requested but the metric has no time_column.")
    col = _column(time_column)
    if start is not None:
        select = select.where(exp.GTE(this=col, expression=_literal(start.isoformat())))
    if end is not None:
        select = select.where(exp.LT(this=col, expression=_literal(end.isoformat())))
    return select


def aggregate_sql(
    metric: Metric, start: datetime | None = None, end: datetime | None = None
) -> str:
    """A single-value aggregate for the metric."""

    select = sqlglot.select(f"{metric.expression} AS value").from_(metric.source_table)
    select = _apply_filters(select, metric)
    select = _apply_period(select, metric.time_column, start, end)
    return select.sql(dialect=_DIALECT)


def timeseries_sql(
    metric: Metric,
    grain: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> str:
    """The metric bucketed by a time grain, ordered chronologically."""

    if not metric.time_column:
        raise AnalysisError(f"Metric has no time_column; cannot build a {grain} time series.")
    bucket = f"DATE_TRUNC('{grain}', {metric.time_column})"
    select = sqlglot.select(f"{bucket} AS period", f"{metric.expression} AS value").from_(
        metric.source_table
    )
    select = _apply_filters(select, metric)
    select = select.where(
        exp.Not(this=exp.Is(this=_column(metric.time_column), expression=exp.Null()))
    )
    select = _apply_period(select, metric.time_column, start, end)
    select = select.group_by("1").order_by("1")
    return select.sql(dialect=_DIALECT)


def segment_sql(
    metric: Metric,
    dimension: Dimension,
    relationships: list[RelationshipInfo],
    *,
    limit: int = 20,
    start: datetime | None = None,
    end: datetime | None = None,
) -> str:
    """The metric broken down by a dimension, ranked by value descending."""

    select = sqlglot.select(f"{dimension.source} AS segment", f"{metric.expression} AS value")
    select = select.from_(metric.source_table)

    for step in _join_path(metric.source_table, dimension.table, relationships):
        on = " AND ".join(
            f"{_table_name(step.present)}.{pc} = {_table_name(step.added)}.{ac}"
            for pc, ac in zip(step.present_columns, step.added_columns, strict=False)
        )
        select = select.join(step.added, on=on, join_type="inner")

    select = _apply_filters(select, metric)
    select = _apply_period(select, metric.time_column, start, end)
    select = select.group_by("1").order_by("value DESC").limit(limit)
    return select.sql(dialect=_DIALECT)


def segment_comparison_sql(
    metric: Metric,
    dimension: Dimension,
    relationships: list[RelationshipInfo],
    *,
    current_start: datetime,
    current_end: datetime,
    baseline_start: datetime,
    baseline_end: datetime,
    limit: int = 20,
) -> str:
    """Compare a metric by segment between two explicit periods.

    The result ranks segments by absolute movement so investigations can identify likely
    drivers instead of showing an all-time aggregate breakdown.
    """

    current_sql = _segment_base_sql(
        metric,
        dimension,
        relationships,
        value_alias="current_value",
        start=current_start,
        end=current_end,
    )
    baseline_sql = _segment_base_sql(
        metric,
        dimension,
        relationships,
        value_alias="baseline_value",
        start=baseline_start,
        end=baseline_end,
    )
    return (
        "WITH current_segments AS ("
        f"{current_sql}"
        "), baseline_segments AS ("
        f"{baseline_sql}"
        "), joined AS ("
        "SELECT COALESCE(c.segment, b.segment) AS segment, "
        "c.current_value, b.baseline_value, "
        "(COALESCE(c.current_value, 0) - COALESCE(b.baseline_value, 0)) AS absolute_change "
        "FROM current_segments c "
        "FULL OUTER JOIN baseline_segments b ON c.segment = b.segment"
        ") "
        "SELECT segment, current_value, baseline_value, absolute_change, "
        "CASE WHEN SUM(ABS(absolute_change)) OVER () = 0 THEN NULL "
        "ELSE ROUND((ABS(absolute_change) / SUM(ABS(absolute_change)) OVER () * 100)::numeric, 2) "
        "END AS contribution_percent "
        "FROM joined "
        "ORDER BY ABS(absolute_change) DESC NULLS LAST "
        f"LIMIT {int(limit)}"
    )


def _segment_base_sql(
    metric: Metric,
    dimension: Dimension,
    relationships: list[RelationshipInfo],
    *,
    value_alias: str,
    start: datetime,
    end: datetime,
) -> str:
    select = sqlglot.select(
        f"{dimension.source} AS segment", f"{metric.expression} AS {value_alias}"
    )
    select = select.from_(metric.source_table)

    for step in _join_path(metric.source_table, dimension.table, relationships):
        on = " AND ".join(
            f"{_table_name(step.present)}.{pc} = {_table_name(step.added)}.{ac}"
            for pc, ac in zip(step.present_columns, step.added_columns, strict=False)
        )
        select = select.join(step.added, on=on, join_type="inner")

    select = _apply_filters(select, metric)
    select = _apply_period(select, metric.time_column, start, end)
    select = select.group_by("1")
    return select.sql(dialect=_DIALECT)


def opportunity_sql(
    primary_metric: Metric,
    secondary_metric: Metric,
    dimension: Dimension,
    relationships: list[RelationshipInfo],
    *,
    limit: int = 20,
    start: datetime | None = None,
    end: datetime | None = None,
) -> str:
    """Rank segments where the primary metric is high and the secondary metric is low.

    This supports questions such as "where is margin strong but volume low" by comparing two
    semantic metrics over the same dimensional breakdown. Both metrics must share a source
    table so the generated query has one clear grain and join path.
    """

    if primary_metric.source_table != secondary_metric.source_table:
        raise AnalysisError("Opportunity analysis requires metrics with the same source_table.")

    select = sqlglot.select(
        f"{dimension.source} AS segment",
        f"{primary_metric.expression} AS primary_value",
        f"{secondary_metric.expression} AS secondary_value",
    )
    select = select.from_(primary_metric.source_table)

    for step in _join_path(primary_metric.source_table, dimension.table, relationships):
        on = " AND ".join(
            f"{_table_name(step.present)}.{pc} = {_table_name(step.added)}.{ac}"
            for pc, ac in zip(step.present_columns, step.added_columns, strict=False)
        )
        select = select.join(step.added, on=on, join_type="inner")

    select = _apply_filters(select, primary_metric)
    select = _apply_filters(select, secondary_metric)
    select = _apply_period(select, primary_metric.time_column, start, end)
    if secondary_metric.time_column and secondary_metric.time_column != primary_metric.time_column:
        select = _apply_period(select, secondary_metric.time_column, start, end)
    select = select.group_by("1")

    segments_sql = select.sql(dialect=_DIALECT)
    return (
        "WITH segments AS ("
        f"{segments_sql}"
        "), ranked AS ("
        "SELECT segment, primary_value, secondary_value, "
        "PERCENT_RANK() OVER (ORDER BY primary_value) AS primary_rank, "
        "PERCENT_RANK() OVER (ORDER BY secondary_value) AS secondary_rank "
        "FROM segments "
        "WHERE primary_value IS NOT NULL AND secondary_value IS NOT NULL"
        ") "
        "SELECT segment, primary_value, secondary_value, "
        "ROUND(((primary_rank + (1 - secondary_rank)) / 2.0)::numeric, 4) AS opportunity_score "
        "FROM ranked "
        "ORDER BY opportunity_score DESC, primary_value DESC, secondary_value ASC "
        f"LIMIT {int(limit)}"
    )


# -- join-path finding -----------------------------------------------------------------------


@dataclass
class _JoinStep:
    present: str  # already-joined table (schema.table)
    added: str  # newly joined table (schema.table)
    present_columns: list[str]
    added_columns: list[str]


@dataclass
class _Edge:
    a: str
    b: str
    a_columns: list[str]
    b_columns: list[str]


def _qualify(table: str) -> str:
    return table if "." in table else f"public.{table}"


def _table_name(qualified: str) -> str:
    return qualified.split(".")[-1]


def _join_path(
    source_table: str, dimension_table: str, relationships: list[RelationshipInfo]
) -> list[_JoinStep]:
    start = _qualify(source_table)
    end = _qualify(dimension_table)
    if start == end:
        return []

    graph: dict[str, list[_Edge]] = defaultdict(list)
    for rel in relationships:
        a = f"{rel.source_schema}.{rel.source_table}"
        b = f"{rel.target_schema}.{rel.target_table}"
        edge = _Edge(a, b, rel.source_columns, rel.target_columns)
        graph[a].append(edge)
        graph[b].append(edge)

    queue: deque[tuple[str, list[_JoinStep]]] = deque([(start, [])])
    visited = {start}
    while queue:
        current, path = queue.popleft()
        for edge in graph[current]:
            neighbour = edge.b if edge.a == current else edge.a
            if neighbour in visited:
                continue
            if edge.a == current:
                step = _JoinStep(current, neighbour, edge.a_columns, edge.b_columns)
            else:
                step = _JoinStep(current, neighbour, edge.b_columns, edge.a_columns)
            new_path = [*path, step]
            if neighbour == end:
                return new_path
            visited.add(neighbour)
            queue.append((neighbour, new_path))

    raise JoinPathError(
        f"No join path found from '{source_table}' to '{dimension_table}'. "
        "Run 'insyte scan', or add a foreign key / relationship."
    )
