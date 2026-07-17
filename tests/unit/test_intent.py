"""Unit tests for the chat intent parser."""

from __future__ import annotations

import pytest

from insyte.analytics.models import TimeGrain
from insyte.semantic.models import Dimension, Metric, MetricStatus, SemanticAlias, SemanticLayer
from insyte.tui.intent import AnalysisMode, IntentKind, parse_intent


@pytest.fixture
def layer() -> SemanticLayer:
    return SemanticLayer(
        metrics={
            "completed_revenue": Metric(
                label="Completed revenue", expression="SUM(x)", source_table="public.orders"
            ),
            "payment_failure_rate": Metric(
                label="Payment failure rate", expression="AVG(y)", source_table="public.payments"
            ),
            "order_count": Metric(
                label="Order count", expression="COUNT(*)", source_table="public.orders"
            ),
        },
        dimensions={
            "city": Dimension(source="cities.name"),
            "payment_method": Dimension(source="payments.method"),
        },
        aliases={
            "orders": SemanticAlias(target="order_count", confidence=0.9),
            "order total": SemanticAlias(target="completed_revenue", confidence=0.84),
        },
    )


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("/help", IntentKind.help),
        ("/metrics", IntentKind.metrics),
        ("/schema", IntentKind.schema),
        ("/history", IntentKind.history),
        ("/clear", IntentKind.clear),
        ("/quit", IntentKind.quit),
    ],
)
def test_slash_commands(text: str, kind: IntentKind, layer: SemanticLayer) -> None:
    assert parse_intent(text, layer).kind is kind


def test_table_command(layer: SemanticLayer) -> None:
    intent = parse_intent("/table orders", layer)
    assert intent.kind is IntentKind.table
    assert intent.argument == "orders"


def test_unknown_slash(layer: SemanticLayer) -> None:
    intent = parse_intent("/bogus", layer)
    assert intent.kind is IntentKind.unknown
    assert intent.argument == "bogus"


def test_timeseries_by_grain_word(layer: SemanticLayer) -> None:
    intent = parse_intent("weekly completed revenue", layer)
    assert intent.kind is IntentKind.analysis
    assert intent.mode is AnalysisMode.timeseries
    assert intent.grain is TimeGrain.week
    assert intent.metric == "completed_revenue"


def test_timeseries_by_clause(layer: SemanticLayer) -> None:
    intent = parse_intent("completed revenue by month", layer)
    assert intent.mode is AnalysisMode.timeseries
    assert intent.grain is TimeGrain.month


def test_segment_by_dimension(layer: SemanticLayer) -> None:
    intent = parse_intent("completed revenue by city", layer)
    assert intent.mode is AnalysisMode.segment
    assert intent.dimension == "city"
    assert intent.metric == "completed_revenue"


def test_segment_multiword_dimension(layer: SemanticLayer) -> None:
    intent = parse_intent("order count by payment method", layer)
    assert intent.mode is AnalysisMode.segment
    assert intent.dimension == "payment_method"


def test_aggregate_default(layer: SemanticLayer) -> None:
    intent = parse_intent("payment failure rate", layer)
    assert intent.mode is AnalysisMode.aggregate
    assert intent.metric == "payment_failure_rate"


def test_metric_alias_resolves_unexpected_phrase(layer: SemanticLayer) -> None:
    intent = parse_intent("what caused orders to change this month", layer)
    assert intent.kind is IntentKind.analysis
    assert intent.metric == "order_count"


def test_low_confidence_alias_does_not_resolve(layer: SemanticLayer) -> None:
    layer.aliases["random business term"] = SemanticAlias(target="order_count", confidence=0.72)
    assert parse_intent("random business term", layer).kind is IntentKind.unknown


def test_suggested_alias_does_not_resolve_before_approval(layer: SemanticLayer) -> None:
    layer.metrics["order_count"].requires_confirmation = True
    layer.aliases["products sold"] = SemanticAlias(
        target="order_count", confidence=0.88, status=MetricStatus.suggested
    )
    assert parse_intent("products sold", layer).kind is IntentKind.unknown


def test_ambiguous_alias_does_not_resolve(layer: SemanticLayer) -> None:
    layer.metrics["sales_order_count"] = Metric(
        label="Sales order count", expression="COUNT(*)", source_table="public.sales_orders"
    )
    layer.aliases["business volume"] = SemanticAlias(target="order_count", confidence=0.84)
    layer.aliases["business volume total"] = SemanticAlias(
        target="sales_order_count", confidence=0.82
    )
    assert parse_intent("business volume total", layer).kind is IntentKind.unknown


def test_compare(layer: SemanticLayer) -> None:
    intent = parse_intent("compare completed revenue", layer)
    assert intent.mode is AnalysisMode.compare
    assert intent.grain is TimeGrain.month  # default grain for comparison


def test_unrecognised_metric(layer: SemanticLayer) -> None:
    assert parse_intent("show me something random", layer).kind is IntentKind.unknown


def test_empty(layer: SemanticLayer) -> None:
    assert parse_intent("   ", layer).kind is IntentKind.unknown
