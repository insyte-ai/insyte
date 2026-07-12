"""Unit tests for the analytics engine with a fake executor (no database)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from insyte.analytics.engine import AnalyticsEngine
from insyte.analytics.models import AnalysisKind, ChartType, Period, TimeGrain
from insyte.exceptions import DimensionNotFoundError, MetricNotFoundError
from insyte.metadata.models import RelationshipInfo
from insyte.query.models import ExecutionResult
from insyte.semantic.models import Dimension, Metric, MetricFormat, SemanticLayer


class FakeExecutor:
    """Returns queued ExecutionResults in order and records executed SQL."""

    def __init__(self, results: list[ExecutionResult]) -> None:
        self._results = list(results)
        self.executed: list[str] = []

    def execute(self, sql: str, *, source: str = "direct") -> ExecutionResult:
        self.executed.append(sql)
        return self._results.pop(0)


def _result(columns: list[str], rows: list[tuple[object, ...]]) -> ExecutionResult:
    return ExecutionResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=False,
        duration_ms=1.0,
        applied_limit=500,
        normalized_sql="SELECT ...",
        referenced_tables=[],
    )


def _layer() -> SemanticLayer:
    return SemanticLayer(
        metrics={
            "revenue": Metric(
                label="Revenue",
                expression="SUM(orders.total_amount)",
                source_table="public.orders",
                time_column="orders.completed_at",
                format=MetricFormat.currency,
            ),
            "margin_rate": Metric(
                label="Margin rate",
                expression="AVG(orders.margin_rate)",
                source_table="public.orders",
                time_column="orders.completed_at",
                format=MetricFormat.percent,
            ),
            "units_sold": Metric(
                label="Units sold",
                expression="SUM(orders.quantity)",
                source_table="public.orders",
                time_column="orders.completed_at",
                format=MetricFormat.number,
            ),
        },
        dimensions={"city": Dimension(source="cities.name")},
    )


def _rels() -> list[RelationshipInfo]:
    return [
        RelationshipInfo(
            "public",
            "orders",
            ["customer_id"],
            "public",
            "customers",
            ["id"],
            "foreign_key",
            1.0,
            None,
        ),
        RelationshipInfo(
            "public", "customers", ["city_id"], "public", "cities", ["id"], "foreign_key", 1.0, None
        ),
    ]


def test_aggregate() -> None:
    ex = FakeExecutor([_result(["value"], [(8_700_000,)])])
    engine = AnalyticsEngine(ex, _layer(), [])
    result = engine.aggregate("revenue")
    assert result.kind is AnalysisKind.aggregate
    assert result.formatted_rows == [["₹87.00 L"]]
    assert result.chart.type is ChartType.none


def test_timeseries() -> None:
    rows = [(datetime(2026, 5, 1, tzinfo=UTC), 100), (datetime(2026, 5, 8, tzinfo=UTC), 200)]
    ex = FakeExecutor([_result(["period", "value"], rows)])
    engine = AnalyticsEngine(ex, _layer(), [])
    result = engine.timeseries("revenue", TimeGrain.week)
    assert result.kind is AnalysisKind.timeseries
    assert result.chart.type is ChartType.line
    assert result.row_count == 2
    assert result.formatted_rows[0][1] == "₹100"  # value column formatted (currency)


def test_segment_uses_join_path() -> None:
    ex = FakeExecutor([_result(["segment", "value"], [("Bengaluru", 400), ("Mumbai", 200)])])
    engine = AnalyticsEngine(ex, _layer(), _rels())
    result = engine.segment("revenue", "city")
    assert result.kind is AnalysisKind.segment
    assert result.contributors[0].segment == "Bengaluru"
    assert "cities" in ex.executed[0]  # join path reached cities
    assert "Bengaluru" in result.summary


def test_segment_compare_ranks_segment_movement() -> None:
    ex = FakeExecutor(
        [
            _result(
                [
                    "segment",
                    "current_value",
                    "baseline_value",
                    "absolute_change",
                    "contribution_percent",
                ],
                [("Bengaluru", 300, 500, -200, 80), ("Mumbai", 100, 150, -50, 20)],
            )
        ]
    )
    engine = AnalyticsEngine(ex, _layer(), _rels())
    current = Period(
        "Mar 2026",
        datetime(2026, 3, 1, tzinfo=UTC),
        datetime(2026, 4, 1, tzinfo=UTC),
    )
    baseline = Period(
        "Feb 2026",
        datetime(2026, 2, 1, tzinfo=UTC),
        datetime(2026, 3, 1, tzinfo=UTC),
    )

    result = engine.segment_compare("revenue", "city", current, baseline)

    assert result.kind is AnalysisKind.segment
    assert result.formatted_rows[0][3] == "₹-200"
    assert "Bengaluru" in result.summary
    assert "current_segments" in ex.executed[0]


def test_opportunity_ranks_segments() -> None:
    ex = FakeExecutor(
        [
            _result(
                ["segment", "primary_value", "secondary_value", "opportunity_score"],
                [("Bengaluru", 0.42, 12, 0.91), ("Mumbai", 0.35, 20, 0.75)],
            )
        ]
    )
    engine = AnalyticsEngine(ex, _layer(), _rels())
    result = engine.opportunity("margin_rate", "units_sold", "city")
    assert result.kind is AnalysisKind.opportunity
    assert result.formatted_rows[0] == ["Bengaluru", "42.0%", "12", "91%"]
    assert "PERCENT_RANK" in ex.executed[0]
    assert "Bengaluru" in result.summary


def test_compare_runs_two_queries() -> None:
    ex = FakeExecutor([_result(["value"], [(760,)]), _result(["value"], [(870,)])])
    engine = AnalyticsEngine(ex, _layer(), [])
    now = datetime(2026, 6, 1, tzinfo=UTC)
    cmp = engine.compare(
        "revenue",
        Period("June", now, now),
        Period("May", now, now),
    )
    assert len(ex.executed) == 2
    assert cmp.absolute_change == -110.0


def test_unknown_metric() -> None:
    engine = AnalyticsEngine(FakeExecutor([]), _layer(), [])
    with pytest.raises(MetricNotFoundError):
        engine.aggregate("ghost")


def test_unknown_dimension() -> None:
    engine = AnalyticsEngine(FakeExecutor([]), _layer(), [])
    with pytest.raises(DimensionNotFoundError):
        engine.segment("revenue", "ghost")
