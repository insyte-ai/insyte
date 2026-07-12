"""Unit tests for Studio Investigation Mode Lite."""

from __future__ import annotations

from typing import cast

from insyte.semantic.models import Dimension, Metric, SemanticLayer
from insyte.services.analysis_service import AnalysisService
from insyte.studio.investigation import InvestigationService, is_investigation_question
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
