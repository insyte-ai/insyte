"""Unit tests for Studio Investigation Mode Lite."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from insyte.analytics.models import TimeGrain
from insyte.semantic.models import Dimension, Metric, SemanticLayer
from insyte.services.analysis_service import AnalysisService
from insyte.studio.investigation import (
    InvestigationService,
    is_investigation_question,
    parse_period_pair,
    parse_relative_period_pair,
)
from insyte.studio.schemas import DataFreshness
from insyte.tui.intent import AnalysisMode, Intent, IntentKind


class _UnusedAnalysis:
    pass


def _intent() -> Intent:
    return Intent(
        IntentKind.analysis,
        mode=AnalysisMode.aggregate,
        metric="sales",
        raw="why did sales change",
    )


def test_detects_metric_investigation_question() -> None:
    assert is_investigation_question("why did sales drop last month", _intent())
    assert is_investigation_question("what caused order count to change this month", _intent())
    assert not is_investigation_question("show sales by city", _intent())
    assert not is_investigation_question(
        "why did it happen",
        Intent(IntentKind.analysis, mode=AnalysisMode.aggregate, raw="why did it happen"),
    )


def test_plan_skips_time_steps_when_metric_has_no_time_column() -> None:
    layer = SemanticLayer(
        metrics={
            "sales": Metric(
                label="Sales",
                expression="SUM(orders.amount)",
                source_table="orders",
            )
        },
        dimensions={"city": Dimension(source="customers.city", label="City")},
    )
    service = InvestigationService(
        analysis=cast(AnalysisService, _UnusedAnalysis()),
        layer=layer,
        freshness=DataFreshness(mode="direct"),
        suggestions=[],
    )

    plan = service.plan("why did sales change", _intent())

    statuses = {step.id: step.status for step in plan.steps}
    assert statuses["trend"] == "skipped"
    assert statuses["current_vs_previous"] == "skipped"
    assert statuses["segment_breakdown"] == "pending"


def test_parse_period_pair_uses_requested_months() -> None:
    periods = parse_period_pair("Why did order count drop from February 2026 to March 2026?")
    assert periods is not None
    current, baseline = periods

    assert current.label == "Mar 2026"
    assert baseline.label == "Feb 2026"


def test_parse_period_pair_handles_versus_chronologically() -> None:
    periods = parse_period_pair("Why did order count change February 2026 vs March 2026?")
    assert periods is not None
    current, baseline = periods

    assert current.label == "Mar 2026"
    assert baseline.label == "Feb 2026"


def test_plan_stores_explicit_comparison_periods() -> None:
    layer = SemanticLayer(
        metrics={
            "sales": Metric(
                label="Sales",
                expression="SUM(orders.amount)",
                source_table="orders",
                time_column="orders.created_at",
            )
        },
        dimensions={"city": Dimension(source="customers.city", label="City")},
    )
    service = InvestigationService(
        analysis=cast(AnalysisService, _UnusedAnalysis()),
        layer=layer,
        freshness=DataFreshness(mode="direct"),
        suggestions=[],
    )

    plan = service.plan(
        "Why did sales drop from February 2026 to March 2026?",
        Intent(
            IntentKind.analysis,
            mode=AnalysisMode.aggregate,
            metric="sales",
            raw="Why did sales drop from February 2026 to March 2026?",
        ),
    )

    assert plan.period == "Mar 2026 vs Feb 2026"
    assert plan.current_period is not None
    assert plan.current_period.label == "Mar 2026"
    assert plan.baseline_period is not None
    assert plan.baseline_period.label == "Feb 2026"
    assert "Mar 2026" in plan.steps[1].title
    assert "Feb 2026" in plan.steps[2].title


def test_this_week_uses_daily_trend_and_matched_previous_week() -> None:
    periods = parse_relative_period_pair(
        "Why has return count increased this week?",
        now=datetime(2026, 7, 16, 10, tzinfo=UTC),
    )
    assert periods is not None
    current, baseline, comparison_grain, trend_grain = periods

    assert current.start == datetime(2026, 7, 13, tzinfo=UTC)
    assert current.end == datetime(2026, 7, 16, 10, tzinfo=UTC)
    assert baseline.start == datetime(2026, 7, 6, tzinfo=UTC)
    assert baseline.end == datetime(2026, 7, 9, 10, tzinfo=UTC)
    assert comparison_grain is TimeGrain.week
    assert trend_grain is TimeGrain.day
