"""Unit tests for the MCP tool service (real SQL executor, fake engine)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection

from insyte.analytics.models import (
    AnalysisKind,
    AnalysisResult,
    ChartSpec,
    ChartType,
    Contributor,
    PeriodComparison,
)
from insyte.config.models import DatabaseSection, InsyteConfig, ProjectSection, QuerySection
from insyte.connectors.base import ConnectionCheckResult, DatabaseConnector
from insyte.mcp.tools import AnalyticsBundle, InsyteToolService
from insyte.metadata.models import (
    Relationship,
    RelationshipKind,
    ScannedColumn,
    ScannedForeignKey,
    ScannedTable,
    ScanResult,
    TableCategory,
    TableKind,
)
from insyte.metadata.repository import MetadataRepository
from insyte.semantic.models import Dimension, Metric, SemanticLayer


class SqliteConnector(DatabaseConnector):
    def __init__(self) -> None:
        self._engine: Engine = create_engine("sqlite://")
        with self._engine.begin() as conn:
            conn.execute(text("CREATE TABLE orders (id integer, city text, total integer)"))
            conn.execute(text("INSERT INTO orders VALUES (1,'Bengaluru',100),(2,'Mumbai',200)"))

    @property
    def host(self) -> str | None:
        return None

    @property
    def port(self) -> int | None:
        return None

    def check_connection(self) -> ConnectionCheckResult:  # pragma: no cover
        raise NotImplementedError

    @contextmanager
    def read_only_transaction(self) -> Iterator[Connection]:
        with self._engine.connect() as conn:
            yield conn

    def dispose(self) -> None:
        self._engine.dispose()


class FakeEngine:
    def segment(self, metric, dimension, period=None, limit=20):
        return AnalysisResult(
            kind=AnalysisKind.segment,
            metric=metric,
            label="Revenue",
            columns=["segment", "value"],
            rows=[("Mumbai", 200), ("Bengaluru", 100)],
            formatted_rows=[["Mumbai", "200"], ["Bengaluru", "100"]],
            sql="SELECT ...",
            chart=ChartSpec(ChartType.bar, title="Revenue"),
            summary="Mumbai leads",
            row_count=2,
            duration_ms=1.0,
            contributors=[
                Contributor("Mumbai", 200.0, 0.667),
                Contributor("Bengaluru", 100.0, 0.333),
            ],
        )

    def compare(self, metric, current, baseline):
        return PeriodComparison(
            metric=metric,
            label="Revenue",
            current=current,
            baseline=baseline,
            current_value=200.0,
            baseline_value=100.0,
            absolute_change=100.0,
            percent_change=100.0,
            sql_current="a",
            sql_baseline="b",
            summary="Revenue increased by 100.0%.",
        )


def _config() -> InsyteConfig:
    return InsyteConfig(
        project=ProjectSection(name="demo"),
        database=DatabaseSection(allowed_schemas=["public", "main"]),
        query=QuerySection(),
    )


def _layer() -> SemanticLayer:
    return SemanticLayer(
        metrics={
            "completed_revenue": Metric(
                label="Completed revenue", expression="SUM(total)", source_table="orders"
            )
        },
        dimensions={"city": Dimension(source="orders.city")},
    )


@pytest.fixture
def service(tmp_path: Path) -> InsyteToolService:
    metadata = MetadataRepository(tmp_path / "metadata.sqlite")
    now = datetime.now(UTC)
    metadata.save_scan(
        ScanResult(
            schemas={"public": None},
            tables=[
                ScannedTable(
                    schema="public",
                    name="orders",
                    kind=TableKind.table,
                    columns=[
                        ScannedColumn("id", 0, "integer", nullable=False, is_primary_key=True),
                        ScannedColumn("city", 1, "text", nullable=True),
                    ],
                    primary_key_columns=["id"],
                    foreign_keys=[ScannedForeignKey("fk", ["city"], "public", "cities", ["id"])],
                    category=TableCategory.fact,
                    category_confidence=0.8,
                ),
            ],
            relationships=[
                Relationship(
                    "public",
                    "orders",
                    ["city"],
                    "public",
                    "cities",
                    ["id"],
                    RelationshipKind.foreign_key,
                    1.0,
                    "fk",
                )
            ],
        ),
        started_at=now,
        finished_at=now,
    )
    connector = SqliteConnector()
    from insyte.query.executor import QueryExecutor

    executor = QueryExecutor(connector, _config(), metadata)
    bundle = AnalyticsBundle(executor, FakeEngine())  # type: ignore[arg-type]
    svc = InsyteToolService(_config(), _layer(), metadata, lambda: bundle)
    yield svc
    connector.dispose()
    metadata.dispose()


def test_database_summary(service: InsyteToolService) -> None:
    summary = service.get_database_summary()
    assert summary["scanned"] is True
    assert summary["table_count"] == 1
    assert summary["tables"][0]["name"] == "public.orders"


def test_search_schema(service: InsyteToolService) -> None:
    result = service.search_schema("city")
    assert result["matches"]
    assert "city" in result["matches"][0]["matched_columns"]


def test_describe_table(service: InsyteToolService) -> None:
    result = service.describe_table("orders")
    assert result["table"] == "public.orders"
    assert any(c["name"] == "city" for c in result["columns"])
    assert result["references"]


def test_list_and_get_metric(service: InsyteToolService) -> None:
    metrics = service.list_metrics()
    assert metrics["metrics"][0]["name"] == "completed_revenue"
    definition = service.get_metric_definition("completed_revenue")
    assert definition["expression"] == "SUM(total)"
    assert "error" in service.get_metric_definition("ghost")


def test_create_analysis_plan(service: InsyteToolService) -> None:
    plan = service.create_analysis_plan("completed revenue by city")
    assert plan["recognized"] is True
    assert plan["intent"] == "segment"
    assert plan["dimension"] == "city"
    assert plan["suggested_tool"] == "insyte_segment_metric"


def test_run_safe_sql_ok_and_audited(service: InsyteToolService) -> None:
    result = service.run_safe_sql("SELECT city, sum(total) AS revenue FROM orders GROUP BY city")
    assert result["ok"] is True
    assert result["row_count"] == 2
    # Audited: history now has the query.
    history = service.get_query_history()
    assert history["queries"][0]["status"] == "ok"


def test_run_safe_sql_blocked_not_executed(service: InsyteToolService) -> None:
    result = service.run_safe_sql("DROP TABLE orders")
    assert result["ok"] is False
    assert result["blocked"] is True
    assert result["violations"]


def test_segment_metric(service: InsyteToolService) -> None:
    result = service.segment_metric("completed_revenue", "city")
    assert result["ok"] is True
    assert result["contributors"][0]["segment"] == "Mumbai"


def test_compare_periods(service: InsyteToolService) -> None:
    result = service.compare_periods("completed_revenue", "month")
    assert result["ok"] is True
    assert result["absolute_change"] == 100.0


def test_compare_bad_grain(service: InsyteToolService) -> None:
    assert service.compare_periods("completed_revenue", "fortnight")["ok"] is False


def test_generate_chart_spec(service: InsyteToolService) -> None:
    spec = service.generate_chart_spec("timeseries", ["period", "value"], 6, "Revenue")
    assert spec["type"] == "line"
    assert "error" in service.generate_chart_spec("bogus", [], 0)
