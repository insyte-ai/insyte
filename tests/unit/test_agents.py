"""Safety and validation tests for internal Month 4 agents."""

from __future__ import annotations

from datetime import UTC, datetime

from insyte.agents.critic import CriticAgent
from insyte.agents.planner import PlannerAgent
from insyte.agents.quality import QualityAgent
from insyte.metadata.models import CardinalityCategory, ColumnProfile
from insyte.nl.llm import Backend
from insyte.semantic.catalog import SemanticCatalog
from insyte.semantic.models import Dimension, Metric, SemanticLayer
from insyte.studio.schemas import DataFreshness, DetailedReport


def _layer() -> SemanticLayer:
    return SemanticLayer(
        metrics={
            "order_count": Metric(
                label="Order count",
                expression="COUNT(*)",
                source_table="public.orders",
                time_column="orders.created_at",
            )
        },
        dimensions={"status": Dimension(source="orders.status", label="Status")},
    )


def test_planner_rejects_unknown_operations_and_dimensions() -> None:
    assert (
        PlannerAgent._validate(
            {
                "metric": "order_count",
                "dimension": "secret_column",
                "operations": ["trend", "quality", "report"],
            },
            "order_count",
            ["status"],
            True,
        )
        is None
    )
    assert (
        PlannerAgent._validate(
            {
                "metric": "order_count",
                "dimension": None,
                "operations": ["arbitrary_sql", "quality", "report"],
            },
            "order_count",
            ["status"],
            True,
        )
        is None
    )


def test_planner_accepts_only_typed_safe_plan() -> None:
    decision = PlannerAgent(_layer(), SemanticCatalog(_layer()))._validate(
        {
            "metric": "order_count",
            "dimension": "status",
            "operations": ["trend", "comparison", "segment", "quality", "report"],
            "confidence": "medium",
        },
        "order_count",
        ["status"],
        True,
    )

    assert decision is not None
    assert [operation.value for operation in decision.operations] == [
        "trend",
        "comparison",
        "segment",
        "quality",
        "report",
    ]


def test_planner_discards_model_plan_with_sql(monkeypatch) -> None:  # noqa: ANN001
    from insyte.agents import planner

    monkeypatch.setattr(
        planner,
        "_run",
        lambda *_args: (
            '{"metric":"order_count","dimension":"status",'
            '"operations":["quality","report"],"sql":"SELECT * FROM orders"}'
        ),
    )
    layer = _layer()
    decision = PlannerAgent(layer, SemanticCatalog(layer)).plan(
        "Why did orders change?", "order_count", [Backend("codex", ["codex"])]
    )

    assert decision is None


def test_critic_blocks_unsupported_report_figures() -> None:
    report = DetailedReport(tl_dr="Order count increased by 42%.")
    review = CriticAgent().review(report, {"order_count": 10, "percent_change": 8})

    assert review.approved is False
    assert review.action == "block"
    assert "42" in review.unsupported_claims[0]


def test_critic_accepts_figures_present_in_evidence() -> None:
    report = DetailedReport(tl_dr="Order count increased by 8% to 10.")
    review = CriticAgent().review(report, {"order_count": 10, "percent_change": 8})

    assert review.approved is True


def test_quality_agent_uses_persisted_null_profile() -> None:
    profile = ColumnProfile(
        schema="public",
        table="orders",
        column="created_at",
        null_fraction=0.25,
        distinct_estimate=75,
        duplicate_ratio=0.1,
        cardinality=CardinalityCategory.high,
        sampled_rows=100,
    )
    assessment = QualityAgent([profile]).assess(
        "order_count",
        _layer().metrics["order_count"],
        DataFreshness(mode="direct", last_scan=datetime.now(UTC)),
    )

    assert len(assessment.issues) == 1
    assert assessment.issues[0].affected == "public.orders.created_at"
    assert "25% nulls" in assessment.issues[0].impact
