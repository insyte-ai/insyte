"""Unit tests for the semantic layer models and repository."""

from __future__ import annotations

from pathlib import Path

from insyte.semantic.models import (
    Dimension,
    Metric,
    MetricFormat,
    MetricStatus,
    SemanticAlias,
    SemanticLayer,
    StarterQuestion,
)
from insyte.semantic.repository import SemanticRepository

_YAML = """
metrics:
  completed_revenue:
    label: Completed revenue
    expression: SUM(orders.total_amount)
    source_table: public.orders
    filters:
      orders.status: [completed]
    time_column: orders.completed_at
    format: currency
    status: confirmed
    confidence: 0.9
dimensions:
  city:
    source: cities.name
    type: categorical
aliases:
  order count:
    target: completed_revenue
    target_type: metric
    confidence: 0.81
    evidence:
      - metric:completed_revenue
"""


def test_load_semantic(tmp_path: Path) -> None:
    path = tmp_path / "semantic.yaml"
    path.write_text(_YAML)
    layer = SemanticRepository(path).load()
    metric = layer.metrics["completed_revenue"]
    assert metric.expression == "SUM(orders.total_amount)"
    assert metric.filters == {"orders.status": ["completed"]}
    assert metric.format is MetricFormat.currency
    assert metric.status is MetricStatus.confirmed
    assert layer.dimensions["city"].source == "cities.name"
    assert layer.aliases["order count"].target == "completed_revenue"
    assert layer.aliases["order count"].confidence == 0.81


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    layer = SemanticRepository(tmp_path / "none.yaml").load()
    assert layer.is_empty()


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "semantic.yaml"
    layer = SemanticLayer(
        metrics={
            "m": Metric(label="M", expression="COUNT(*)", source_table="public.t"),
        },
        dimensions={"d": Dimension(source="t.col")},
        aliases={
            "business volume": SemanticAlias(target="m", confidence=0.83, evidence=["metric:m"])
        },
        starter_questions=[
            StarterQuestion(
                question="How is business volume trending monthly?",
                metric="m",
                mode="timeseries",
                generated_by="codex",
            )
        ],
    )
    repo = SemanticRepository(path)
    repo.save(layer)
    reloaded = repo.load()
    assert reloaded.metrics["m"].expression == "COUNT(*)"
    assert reloaded.dimensions["d"].source == "t.col"
    assert reloaded.aliases["business volume"].evidence == ["metric:m"]
    assert reloaded.starter_questions[0].generated_by == "codex"


def test_repository_cache_returns_defensive_copies_and_detects_file_edits(tmp_path: Path) -> None:
    path = tmp_path / "semantic.yaml"
    path.write_text(_YAML)
    repo = SemanticRepository(path)
    first = repo.load()
    first.metrics.clear()
    assert "completed_revenue" in repo.load().metrics

    path.write_text(_YAML.replace("Completed revenue", "Net completed revenue"))
    assert repo.load().metrics["completed_revenue"].label == "Net completed revenue"


def test_dimension_table_property() -> None:
    assert Dimension(source="cities.name").table == "cities"
    assert Dimension(source="public.cities.name").table == "public.cities"
