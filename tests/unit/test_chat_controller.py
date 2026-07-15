"""Unit tests for the chat controller (fake engine, real metadata store)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from insyte.analytics.models import AnalysisKind, AnalysisResult, ChartSpec, ChartType
from insyte.exceptions import MetricNotFoundError
from insyte.metadata.models import (
    ScannedColumn,
    ScannedTable,
    ScanResult,
    TableCategory,
    TableKind,
)
from insyte.metadata.repository import MetadataRepository
from insyte.query.models import QueryHistoryEntry
from insyte.semantic.models import Dimension, Metric, SemanticLayer
from insyte.tui.controller import ChatController, ResponseKind


class FakeEngine:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[str] = []
        self._error = error

    def _maybe_raise(self) -> None:
        if self._error is not None:
            raise self._error

    def aggregate(self, metric, period=None):
        self.calls.append("aggregate")
        self._maybe_raise()
        return _analysis(AnalysisKind.aggregate)

    def timeseries(self, metric, grain, period=None):
        self.calls.append("timeseries")
        return _analysis(AnalysisKind.timeseries)

    def segment(self, metric, dimension, period=None, limit=20):
        self.calls.append("segment")
        return _analysis(AnalysisKind.segment)


def _analysis(kind: AnalysisKind) -> AnalysisResult:
    return AnalysisResult(
        kind=kind,
        metric="completed_revenue",
        label="Completed revenue",
        columns=["value"],
        rows=[(100,)],
        formatted_rows=[["100"]],
        sql="SELECT ...",
        chart=ChartSpec(ChartType.none, title="Completed revenue"),
        summary="ok",
        row_count=1,
        duration_ms=1.0,
    )


def _layer() -> SemanticLayer:
    return SemanticLayer(
        metrics={
            "completed_revenue": Metric(
                label="Completed revenue", expression="SUM(x)", source_table="public.orders"
            ),
            "payment_failure_rate": Metric(
                label="Payment failure rate", expression="AVG(y)", source_table="public.payments"
            ),
        },
        dimensions={"city": Dimension(source="cities.name")},
    )


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests hermetic: never shell out to a real claude/codex CLI for free-form input."""

    monkeypatch.setenv("INSYTE_STUDIO_LLM", "off")


@pytest.fixture
def metadata(tmp_path: Path) -> MetadataRepository:
    repo = MetadataRepository(tmp_path / "metadata.sqlite")
    now = datetime.now(UTC)
    repo.save_scan(
        ScanResult(
            schemas={"public": None},
            tables=[
                ScannedTable(
                    schema="public",
                    name="orders",
                    kind=TableKind.table,
                    columns=[
                        ScannedColumn("id", 0, "integer", nullable=False, is_primary_key=True)
                    ],
                    primary_key_columns=["id"],
                    category=TableCategory.fact,
                    category_confidence=0.8,
                )
            ],
            relationships=[],
        ),
        started_at=now,
        finished_at=now,
    )
    yield repo
    repo.dispose()


def _controller(metadata: MetadataRepository, engine: FakeEngine | None = None) -> ChatController:
    engine = engine or FakeEngine()
    return ChatController(_layer(), metadata, lambda: engine)


def test_help(metadata: MetadataRepository) -> None:
    response = _controller(metadata).run("/help")
    assert response.kind is ResponseKind.message
    assert "Insyte chat" in response.text


def test_metrics(metadata: MetadataRepository) -> None:
    response = _controller(metadata).run("/metrics")
    assert response.kind is ResponseKind.table
    names = {row[0] for row in response.rows}
    assert "completed_revenue" in names
    assert "city" in names  # dimensions included


def test_schema(metadata: MetadataRepository) -> None:
    response = _controller(metadata).run("/schema")
    assert response.kind is ResponseKind.table
    assert any("orders" in row[0] for row in response.rows)


def test_table_detail(metadata: MetadataRepository) -> None:
    response = _controller(metadata).run("/table orders")
    assert response.kind is ResponseKind.table
    assert any(row[0] == "id" for row in response.rows)


def test_table_not_found(metadata: MetadataRepository) -> None:
    response = _controller(metadata).run("/table ghost")
    assert response.level == "error"


def test_history(metadata: MetadataRepository) -> None:
    metadata.record_query(
        QueryHistoryEntry(
            source="direct",
            raw_sql="SELECT 1",
            normalized_sql=None,
            referenced_tables=[],
            status="ok",
        )
    )
    response = _controller(metadata).run("/history")
    assert response.kind is ResponseKind.table
    assert response.rows


def test_analysis_segment_dispatch(metadata: MetadataRepository) -> None:
    engine = FakeEngine()
    response = _controller(metadata, engine).run("completed revenue by city")
    assert response.kind is ResponseKind.analysis
    assert engine.calls == ["segment"]


def test_analysis_aggregate_dispatch(metadata: MetadataRepository) -> None:
    engine = FakeEngine()
    response = _controller(metadata, engine).run("payment failure rate")
    assert response.kind is ResponseKind.analysis
    assert engine.calls == ["aggregate"]


def test_analysis_error_becomes_message(metadata: MetadataRepository) -> None:
    engine = FakeEngine(error=MetricNotFoundError("completed_revenue"))
    response = _controller(metadata, engine).run("completed revenue")
    assert response.kind is ResponseKind.message
    assert response.level == "error"


def test_unknown_input(metadata: MetadataRepository) -> None:
    response = _controller(metadata).run("what is diamond problem in java")
    assert response.kind is ResponseKind.message
    assert "connected business data" in response.text
    assert "diamond" not in response.text.lower()


def test_greeting_and_capabilities(metadata: MetadataRepository) -> None:
    controller = _controller(metadata)
    assert "Hi!" in controller.run("hello").text
    assert "calculate metrics" in controller.run("what can you do?").text


def test_clear_and_quit(metadata: MetadataRepository) -> None:
    controller = _controller(metadata)
    assert controller.run("/clear").kind is ResponseKind.clear
    assert controller.run("/quit").kind is ResponseKind.quit
