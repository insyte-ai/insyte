"""Unit tests for the deterministic detailed-report grounding (analytics/report.py)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from insyte.analytics.models import (
    AnalysisKind,
    AnalysisResult,
    ChartSpec,
    ChartType,
    Contributor,
)
from insyte.analytics.report import (
    MAX_REPORT_ROWS,
    build_report_context,
    contribution_summary,
    data_quality_flags,
    data_thinness_warnings,
    forecast_bands,
    freshness_warnings,
    outlier_flags,
    trend_deltas,
)
from insyte.metadata.models import CardinalityCategory, ColumnProfile
from insyte.semantic.models import Metric, MetricFormat


def _segment_result(rows: list[tuple[object, ...]]) -> AnalysisResult:
    return AnalysisResult(
        kind=AnalysisKind.segment,
        metric="total_amount",
        label="Total amount",
        columns=["city", "value"],
        rows=rows,
        formatted_rows=[[str(a), str(b)] for a, b in rows],
        sql="SELECT ...",
        chart=ChartSpec(ChartType.bar, title="Total amount by city"),
        summary="ok",
        row_count=len(rows),
        duration_ms=1.0,
        contributors=[
            Contributor(segment="Mumbai", value=700000.0, share=0.7),
            Contributor(segment="Delhi", value=300000.0, share=0.3),
        ],
    )


def _profile(table: str, column: str, **kw: object) -> ColumnProfile:
    defaults: dict = {
        "schema": "public",
        "null_fraction": 0.0,
        "distinct_estimate": 10,
        "duplicate_ratio": 0.0,
        "cardinality": CardinalityCategory.medium,
        "sampled_rows": 100,
    }
    defaults.update(kw)
    return ColumnProfile(table=table, column=column, **defaults)  # type: ignore[arg-type]


def test_context_is_grounded_and_json_safe() -> None:
    metric = Metric(
        label="Total amount",
        expression="SUM(x)",
        source_table="public.orders",
        format=MetricFormat.currency,
    )
    domain = _segment_result([("Mumbai", 700000.0), ("Delhi", 300000.0)])
    payload = build_report_context(
        question="revenue by city",
        domain=domain,
        metric=metric,
        fmt=MetricFormat.currency,
        profiles=[],
        period_label="last_month",
        freshness_mode="direct",
        last_scan=datetime(2026, 7, 1, tzinfo=UTC),
        forecast_points=None,
    )
    assert payload["metric"]["name"] == "total_amount"
    assert payload["result_kind"] == "segment"
    assert payload["top_contributors"][0]["segment"] == "Mumbai"
    assert payload["top_contributors"][0]["share_pct"] == 70.0
    assert payload["contribution_summary"]["top_3_share_pct"] == 100.0
    assert payload["freshness"]["last_scan"] == "2026-07-01T00:00:00+00:00"
    assert "forecast" not in payload
    # Must be serialisable as-is (this is what gets embedded in the prompt).
    assert json.loads(json.dumps(payload))["row_count"] == 2


def test_rows_are_capped() -> None:
    rows = [(str(i), float(i)) for i in range(MAX_REPORT_ROWS + 50)]
    domain = _segment_result(rows)
    payload = build_report_context(
        question="q",
        domain=domain,
        metric=None,
        fmt=MetricFormat.number,
        profiles=[],
        period_label=None,
        freshness_mode="direct",
        last_scan=None,
    )
    assert len(payload["rows"]) == MAX_REPORT_ROWS
    assert payload["truncated"] is True


def test_quality_flags_severity_and_table_filter() -> None:
    profiles = [
        _profile("orders", "discount", null_fraction=0.6),  # critical
        _profile("orders", "email", is_pii=True, pii_type="email"),  # info
        _profile("customers", "phone", is_pii=True),  # filtered out (other table)
    ]
    flags = data_quality_flags(profiles, {"public.orders"})
    affected = {f["affected"] for f in flags}
    assert "orders.discount" in affected
    assert "orders.email" in affected
    assert "customers.phone" not in affected  # table filter works
    assert flags[0]["severity"] == "critical"  # sorted most-severe first


def test_forecast_bands_order_and_empty() -> None:
    now = datetime(2026, 7, 15, tzinfo=UTC)
    points = [(datetime(2026, m, 1, tzinfo=UTC), float(m * 100)) for m in range(1, 7)]
    bands = forecast_bands(points, now, MetricFormat.number)
    assert bands is not None
    assert set(bands) >= {"expected", "best_case", "worst_case", "assumptions", "method"}

    # No completed months → no projection.
    jan = datetime(2026, 1, 5, tzinfo=UTC)
    assert (
        forecast_bands([(datetime(2026, 1, 1, tzinfo=UTC), 10.0)], jan, MetricFormat.number) is None
    )


def test_report_insight_helpers() -> None:
    domain = _segment_result(
        [
            ("A", 10.0),
            ("B", 11.0),
            ("C", 12.0),
            ("D", 13.0),
            ("E", 100.0),
        ]
    )
    domain.contributors = [
        Contributor(segment="A", value=50.0, share=0.5),
        Contributor(segment="B", value=25.0, share=0.25),
        Contributor(segment="C", value=10.0, share=0.1),
        Contributor(segment="D", value=10.0, share=0.1),
        Contributor(segment="E", value=5.0, share=0.05),
    ]

    assert contribution_summary(domain)["top_3_share_pct"] == 85.0
    assert outlier_flags(domain, MetricFormat.number)[0]["segment"] == "E"
    assert data_thinness_warnings(_segment_result([("A", 1.0)]))


def test_trend_delta_and_freshness_warnings() -> None:
    trend = AnalysisResult(
        kind=AnalysisKind.timeseries,
        metric="total_amount",
        label="Total amount",
        columns=["month", "value"],
        rows=[
            (datetime(2026, 5, 1, tzinfo=UTC), 100.0),
            (datetime(2026, 6, 1, tzinfo=UTC), 150.0),
        ],
        formatted_rows=[],
        sql="SELECT ...",
        chart=ChartSpec(ChartType.line, title="Monthly"),
        summary="ok",
        row_count=2,
        duration_ms=1.0,
    )
    delta = trend_deltas(trend, MetricFormat.number)
    assert delta["absolute_change"] == 50.0
    assert delta["percent_change"] == 50.0
    assert freshness_warnings(
        datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 7, 15, tzinfo=UTC), "warehouse"
    )
