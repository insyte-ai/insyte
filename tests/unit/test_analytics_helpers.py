"""Unit tests for chart recommendation, formatting, segmentation and comparison."""

from __future__ import annotations

from datetime import UTC, datetime

from insyte.analytics.charts import format_value, recommend_chart
from insyte.analytics.comparison import compute_comparison
from insyte.analytics.models import AnalysisKind, ChartType, Period
from insyte.analytics.segmentation import rank_contributors
from insyte.semantic.models import Metric, MetricFormat


def test_recommend_line_for_timeseries() -> None:
    spec = recommend_chart(AnalysisKind.timeseries, ["period", "value"], 6, "Revenue")
    assert spec.type is ChartType.line


def test_recommend_bar_for_small_segment() -> None:
    spec = recommend_chart(AnalysisKind.segment, ["segment", "value"], 3, "Revenue")
    assert spec.type is ChartType.bar


def test_recommend_horizontal_bar_for_many_segments() -> None:
    spec = recommend_chart(AnalysisKind.segment, ["segment", "value"], 20, "Revenue")
    assert spec.type is ChartType.horizontal_bar


def test_no_chart_for_aggregate() -> None:
    spec = recommend_chart(AnalysisKind.aggregate, ["value"], 1, "Revenue")
    assert spec.type is ChartType.none


def test_format_currency_and_percent() -> None:
    assert format_value(8_700_000, MetricFormat.currency) == "₹87.00 L"  # 87 lakh
    assert format_value(250_000_000, MetricFormat.currency) == "₹25.00 Cr"  # 25 crore
    assert format_value(0.124, MetricFormat.percent) == "12.4%"
    assert format_value(1500, MetricFormat.number) == "1.5 K"
    assert format_value(None, MetricFormat.number) == "—"


def test_rank_contributors() -> None:
    contributors = rank_contributors([("Bengaluru", 400), ("Mumbai", 100)])
    assert contributors[0].segment == "Bengaluru"
    assert contributors[0].share == 0.8
    assert contributors[1].share == 0.2


def test_compute_comparison() -> None:
    metric = Metric(
        label="Revenue", expression="SUM(x)", source_table="t", format=MetricFormat.currency
    )
    now = datetime(2026, 6, 1, tzinfo=UTC)
    cmp = compute_comparison(
        "revenue",
        metric,
        Period("June", now, now),
        760.0,
        Period("May", now, now),
        870.0,
        "sqlc",
        "sqlb",
    )
    assert cmp.absolute_change == -110.0
    assert round(cmp.percent_change, 1) == -12.6
    assert "decreased" in cmp.summary


def test_compute_comparison_missing_data() -> None:
    metric = Metric(label="Revenue", expression="SUM(x)", source_table="t")
    p = Period("x", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC))
    cmp = compute_comparison("r", metric, p, None, p, 5.0, "a", "b")
    assert cmp.absolute_change is None
    assert "not enough data" in cmp.summary
