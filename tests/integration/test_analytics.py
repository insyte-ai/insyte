"""Integration test: the analytics engine answers the Milestone 5 acceptance questions.

Runs only when ``INSYTE_TEST_DATABASE_URL`` is set. Builds the ecommerce fixture and the
fixture semantic layer, then checks weekly revenue, revenue by city, and payment failure rate.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from helpers import load_ecommerce_fixture

from insyte.analytics.engine import AnalyticsEngine
from insyte.analytics.models import AnalysisKind, ChartType, TimeGrain
from insyte.config.models import (
    DatabaseSection,
    InsyteConfig,
    ProjectSection,
    QuerySection,
    SSLMode,
)
from insyte.connectors.postgres import PostgresConnector
from insyte.metadata.repository import MetadataRepository, utcnow
from insyte.metadata.scanner import SchemaScanner
from insyte.query.executor import QueryExecutor
from insyte.semantic.repository import SemanticRepository

_TEST_URL = os.environ.get("INSYTE_TEST_DATABASE_URL")
_FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.skipif(
    not _TEST_URL, reason="Set INSYTE_TEST_DATABASE_URL to run PostgreSQL integration tests."
)


@pytest.fixture(scope="module")
def engine(tmp_path_factory: pytest.TempPathFactory):
    assert _TEST_URL is not None
    load_ecommerce_fixture(_TEST_URL, _FIXTURES / "ecommerce.sql")

    config = InsyteConfig(
        project=ProjectSection(name="it"),
        database=DatabaseSection(ssl_mode=SSLMode.prefer),
        query=QuerySection(),
    )
    connector = PostgresConnector(_TEST_URL, config.database, config.query)

    # Scan for relationships, then build the engine.
    metadata_path = tmp_path_factory.mktemp("meta") / "metadata.sqlite"
    repo = MetadataRepository(metadata_path)
    result = SchemaScanner(connector, config.database).scan()
    repo.save_scan(result, started_at=utcnow(), finished_at=utcnow())

    layer = SemanticRepository(_FIXTURES / "semantic.yaml").load()
    executor = QueryExecutor(connector, config, repo)
    yield AnalyticsEngine(executor, layer, repo.list_relationships())
    connector.dispose()
    repo.dispose()


def test_weekly_completed_revenue(engine: AnalyticsEngine) -> None:
    result = engine.timeseries("completed_revenue", TimeGrain.week)
    assert result.kind is AnalysisKind.timeseries
    assert result.row_count >= 1
    assert result.chart.type in {ChartType.line, ChartType.none}


def test_revenue_by_city(engine: AnalyticsEngine) -> None:
    result = engine.segment("completed_revenue", "city")
    assert result.kind is AnalysisKind.segment
    segments = {row[0] for row in result.rows}
    assert {"Bengaluru", "Mumbai"} & segments
    assert result.contributors  # ranked


def test_payment_failure_rate(engine: AnalyticsEngine) -> None:
    result = engine.aggregate("payment_failure_rate")
    # Fixture has 1 failed of 3 payments → ~33%.
    assert result.rows
    assert 0.0 < float(result.rows[0][0]) < 1.0
