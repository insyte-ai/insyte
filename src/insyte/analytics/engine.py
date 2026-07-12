"""The analytics engine: turn a metric request into an executed, formatted result.

The engine resolves a metric (and optional dimension) from the semantic layer, generates safe
SQL, runs it through the Milestone 4 executor (validation + audit + read-only execution), then
formats the rows and recommends a chart.
"""

from __future__ import annotations

from datetime import datetime

from insyte.analytics.charts import format_value, recommend_chart
from insyte.analytics.comparison import compute_comparison
from insyte.analytics.models import (
    AnalysisKind,
    AnalysisResult,
    Period,
    PeriodComparison,
    TimeGrain,
)
from insyte.analytics.segmentation import rank_contributors
from insyte.exceptions import DimensionNotFoundError, MetricNotFoundError
from insyte.metadata.models import RelationshipInfo
from insyte.query.executor import QueryExecutor
from insyte.query.generator import (
    aggregate_sql,
    opportunity_sql,
    segment_comparison_sql,
    segment_sql,
    timeseries_sql,
)
from insyte.query.models import ExecutionResult
from insyte.semantic.models import Dimension, Metric, SemanticLayer

_SOURCE = "analytics"


class AnalyticsEngine:
    """Answer structured analytical questions against a project."""

    def __init__(
        self,
        executor: QueryExecutor,
        layer: SemanticLayer,
        relationships: list[RelationshipInfo],
    ) -> None:
        self._executor = executor
        self._layer = layer
        self._relationships = relationships

    # -- resolution --------------------------------------------------------------------------

    def _metric(self, name: str) -> Metric:
        metric = self._layer.metrics.get(name)
        if metric is None:
            raise MetricNotFoundError(name)
        return metric

    def _dimension(self, name: str) -> Dimension:
        dimension = self._layer.dimensions.get(name)
        if dimension is None:
            raise DimensionNotFoundError(name)
        return dimension

    # -- analyses ----------------------------------------------------------------------------

    def aggregate(self, metric_name: str, period: Period | None = None) -> AnalysisResult:
        metric = self._metric(metric_name)
        start, end = _bounds(period)
        sql = aggregate_sql(metric, start, end)
        execution = self._executor.execute(sql, source=_SOURCE)
        value = _scalar(execution)
        formatted = format_value(value, metric.format)
        suffix = f" ({period.label})" if period else ""
        return AnalysisResult(
            kind=AnalysisKind.aggregate,
            metric=metric_name,
            label=metric.label,
            columns=execution.columns,
            rows=execution.rows,
            formatted_rows=[[formatted]],
            sql=execution.normalized_sql,
            chart=recommend_chart(AnalysisKind.aggregate, execution.columns, 1, metric.label),
            summary=f"{metric.label}: {formatted}{suffix}.",
            row_count=execution.row_count,
            duration_ms=execution.duration_ms,
        )

    def timeseries(
        self, metric_name: str, grain: TimeGrain, period: Period | None = None
    ) -> AnalysisResult:
        metric = self._metric(metric_name)
        start, end = _bounds(period)
        sql = timeseries_sql(metric, grain.value, start, end)
        execution = self._executor.execute(sql, source=_SOURCE)
        formatted = _format_rows(execution.rows, value_index=1, metric=metric)
        latest = format_value(execution.rows[-1][1], metric.format) if execution.rows else "—"
        return AnalysisResult(
            kind=AnalysisKind.timeseries,
            metric=metric_name,
            label=metric.label,
            columns=execution.columns,
            rows=execution.rows,
            formatted_rows=formatted,
            sql=execution.normalized_sql,
            chart=recommend_chart(
                AnalysisKind.timeseries, execution.columns, execution.row_count, metric.label
            ),
            summary=(
                f"{metric.label} by {grain.value}: {execution.row_count} buckets; latest {latest}."
            ),
            row_count=execution.row_count,
            duration_ms=execution.duration_ms,
        )

    def segment(
        self,
        metric_name: str,
        dimension_name: str,
        period: Period | None = None,
        limit: int = 20,
    ) -> AnalysisResult:
        metric = self._metric(metric_name)
        dimension = self._dimension(dimension_name)
        start, end = _bounds(period)
        sql = segment_sql(metric, dimension, self._relationships, limit=limit, start=start, end=end)
        execution = self._executor.execute(sql, source=_SOURCE)
        contributors = rank_contributors(execution.rows)
        formatted = _format_rows(execution.rows, value_index=1, metric=metric)
        summary = _segment_summary(metric, dimension_name, contributors)
        return AnalysisResult(
            kind=AnalysisKind.segment,
            metric=metric_name,
            label=metric.label,
            columns=execution.columns,
            rows=execution.rows,
            formatted_rows=formatted,
            sql=execution.normalized_sql,
            chart=recommend_chart(
                AnalysisKind.segment, execution.columns, execution.row_count, metric.label
            ),
            summary=summary,
            row_count=execution.row_count,
            duration_ms=execution.duration_ms,
            contributors=contributors,
        )

    def segment_compare(
        self,
        metric_name: str,
        dimension_name: str,
        current: Period,
        baseline: Period,
        limit: int = 20,
    ) -> AnalysisResult:
        metric = self._metric(metric_name)
        dimension = self._dimension(dimension_name)
        sql = segment_comparison_sql(
            metric,
            dimension,
            self._relationships,
            current_start=current.start,
            current_end=current.end,
            baseline_start=baseline.start,
            baseline_end=baseline.end,
            limit=limit,
        )
        execution = self._executor.execute(sql, source=_SOURCE)
        formatted = _format_segment_compare_rows(execution.rows, metric=metric)
        contributors = rank_contributors(
            [(row[0], abs(float(row[3] or 0))) for row in execution.rows]
        )
        summary = _segment_compare_summary(
            metric, dimension_name, current, baseline, execution.rows
        )
        return AnalysisResult(
            kind=AnalysisKind.segment,
            metric=metric_name,
            label=metric.label,
            columns=execution.columns,
            rows=execution.rows,
            formatted_rows=formatted,
            sql=execution.normalized_sql,
            chart=recommend_chart(
                AnalysisKind.segment, execution.columns, execution.row_count, metric.label
            ),
            summary=summary,
            row_count=execution.row_count,
            duration_ms=execution.duration_ms,
            contributors=contributors,
        )

    def opportunity(
        self,
        primary_metric_name: str,
        secondary_metric_name: str,
        dimension_name: str,
        period: Period | None = None,
        limit: int = 20,
    ) -> AnalysisResult:
        primary = self._metric(primary_metric_name)
        secondary = self._metric(secondary_metric_name)
        dimension = self._dimension(dimension_name)
        start, end = _bounds(period)
        sql = opportunity_sql(
            primary,
            secondary,
            dimension,
            self._relationships,
            limit=limit,
            start=start,
            end=end,
        )
        execution = self._executor.execute(sql, source=_SOURCE)
        formatted = _format_opportunity_rows(execution.rows, primary=primary, secondary=secondary)
        summary = _opportunity_summary(primary, secondary, dimension_name, execution.rows)
        return AnalysisResult(
            kind=AnalysisKind.opportunity,
            metric=primary_metric_name,
            label=f"{primary.label} high / {secondary.label} low",
            columns=execution.columns,
            rows=execution.rows,
            formatted_rows=formatted,
            sql=execution.normalized_sql,
            chart=recommend_chart(
                AnalysisKind.segment, execution.columns, execution.row_count, primary.label
            ),
            summary=summary,
            row_count=execution.row_count,
            duration_ms=execution.duration_ms,
        )

    def compare(self, metric_name: str, current: Period, baseline: Period) -> PeriodComparison:
        metric = self._metric(metric_name)
        sql_current = aggregate_sql(metric, current.start, current.end)
        sql_baseline = aggregate_sql(metric, baseline.start, baseline.end)
        current_value = _scalar(self._executor.execute(sql_current, source=_SOURCE))
        baseline_value = _scalar(self._executor.execute(sql_baseline, source=_SOURCE))
        return compute_comparison(
            metric_name,
            metric,
            current,
            current_value,
            baseline,
            baseline_value,
            sql_current,
            sql_baseline,
        )


def _bounds(period: Period | None) -> tuple[datetime | None, datetime | None]:
    return (None, None) if period is None else (period.start, period.end)


def _scalar(result: ExecutionResult) -> float | None:
    if not result.rows or not result.rows[0]:
        return None
    try:
        return float(result.rows[0][0])  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _format_rows(
    rows: list[tuple[object, ...]], *, value_index: int, metric: Metric
) -> list[list[str]]:
    formatted: list[list[str]] = []
    for row in rows:
        cells = []
        for index, cell in enumerate(row):
            if index == value_index:
                cells.append(format_value(cell, metric.format))
            elif cell is None:
                cells.append("—")
            else:
                cells.append(str(cell))
        formatted.append(cells)
    return formatted


def _format_opportunity_rows(
    rows: list[tuple[object, ...]], *, primary: Metric, secondary: Metric
) -> list[list[str]]:
    formatted: list[list[str]] = []
    for row in rows:
        cells = []
        for index, cell in enumerate(row):
            if cell is None:
                cells.append("—")
            elif index == 1:
                cells.append(format_value(cell, primary.format))
            elif index == 2:
                cells.append(format_value(cell, secondary.format))
            elif index == 3:
                score = _number(cell)
                if score is None:
                    cells.append(str(cell))
                else:
                    cells.append(f"{score * 100:.0f}%")
            else:
                cells.append(str(cell))
        formatted.append(cells)
    return formatted


def _format_segment_compare_rows(
    rows: list[tuple[object, ...]], *, metric: Metric
) -> list[list[str]]:
    formatted: list[list[str]] = []
    for row in rows:
        cells = []
        for index, cell in enumerate(row):
            if cell is None:
                cells.append("—")
            elif index in {1, 2, 3}:
                cells.append(format_value(cell, metric.format))
            elif index == 4:
                try:
                    cells.append(f"{float(cell):.1f}%")
                except (TypeError, ValueError):
                    cells.append(str(cell))
            else:
                cells.append(str(cell))
        formatted.append(cells)
    return formatted


def _segment_summary(metric: Metric, dimension: str, contributors: list) -> str:
    if not contributors:
        return f"{metric.label} by {dimension}: no data."
    top = contributors[0]
    value = format_value(top.value, metric.format)
    return f"{metric.label} by {dimension}: '{top.segment}' leads with {value} ({top.share:.0%})."


def _segment_compare_summary(
    metric: Metric,
    dimension: str,
    current: Period,
    baseline: Period,
    rows: list[tuple[object, ...]],
) -> str:
    if not rows:
        return (
            f"{metric.label} change by {dimension}: no segment movement from "
            f"{baseline.label} to {current.label}."
        )
    top = rows[0]
    change = format_value(top[3], metric.format) if len(top) > 3 else "—"
    return (
        f"{metric.label} change by {dimension}: '{top[0]}' moved {change} from "
        f"{baseline.label} to {current.label}."
    )


def _number(value: object) -> float | None:
    if isinstance(value, int | float | str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _opportunity_summary(
    primary: Metric, secondary: Metric, dimension: str, rows: list[tuple[object, ...]]
) -> str:
    if not rows:
        return (
            f"No {dimension} segments had high {primary.label.lower()} with low "
            f"{secondary.label.lower()}."
        )
    top = rows[0]
    primary_value = format_value(top[1], primary.format) if len(top) > 1 else "—"
    secondary_value = format_value(top[2], secondary.format) if len(top) > 2 else "—"
    return (
        f"Top {dimension} opportunity: '{top[0]}' has {primary.label.lower()} of "
        f"{primary_value} with {secondary.label.lower()} of {secondary_value}."
    )
