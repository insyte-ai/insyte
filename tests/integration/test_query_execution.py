"""Integration test: the safe pipeline against a real PostgreSQL server.

Runs only when ``INSYTE_TEST_DATABASE_URL`` is set. Builds the ecommerce fixture, then checks
that validated queries execute read-only and that blocked queries never touch the database.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from helpers import load_ecommerce_fixture
from sqlalchemy import text

from insyte.config.models import (
    DatabaseSection,
    InsyteConfig,
    ProjectSection,
    QuerySection,
    SSLMode,
)
from insyte.connectors.postgres import PostgresConnector
from insyte.exceptions import QueryValidationError
from insyte.query.executor import QueryExecutor
from insyte.query.models import QueryHistoryEntry, SecurityEventEntry

_TEST_URL = os.environ.get("INSYTE_TEST_DATABASE_URL")
_FIXTURE = Path(__file__).parent.parent / "fixtures" / "ecommerce.sql"

pytestmark = pytest.mark.skipif(
    not _TEST_URL, reason="Set INSYTE_TEST_DATABASE_URL to run PostgreSQL integration tests."
)


class _Recorder:
    def __init__(self) -> None:
        self.queries: list[QueryHistoryEntry] = []
        self.events: list[SecurityEventEntry] = []

    def record_query(self, entry: QueryHistoryEntry) -> None:
        self.queries.append(entry)

    def record_security_event(self, event: SecurityEventEntry) -> None:
        self.events.append(event)


@pytest.fixture(scope="module")
def executor():
    assert _TEST_URL is not None
    load_ecommerce_fixture(_TEST_URL, _FIXTURE)

    config = InsyteConfig(
        project=ProjectSection(name="it"),
        database=DatabaseSection(ssl_mode=SSLMode.prefer, blocked_columns=["customers.email"]),
        query=QuerySection(default_limit=100),
    )
    connector = PostgresConnector(_TEST_URL, config.database, config.query)
    recorder = _Recorder()
    yield QueryExecutor(connector, config, recorder), recorder
    connector.dispose()


def test_validated_query_executes(executor) -> None:
    ex, recorder = executor
    result = ex.execute(
        "SELECT c.name, count(o.id) AS orders "
        "FROM customers c JOIN orders o ON o.customer_id = c.id GROUP BY c.name"
    )
    assert result.row_count >= 1
    assert result.applied_limit == 100
    assert recorder.queries[-1].status == "ok"


def test_blocked_query_never_reaches_db(executor) -> None:
    ex, recorder = executor
    with pytest.raises(QueryValidationError):
        ex.execute("DELETE FROM orders")
    assert recorder.events[-1].event_type == "blocked_query"
    # Orders still intact.
    with ex._connector.read_only_transaction() as conn:  # noqa: SLF001 - test introspection
        assert conn.execute(text("SELECT count(*) FROM orders")).scalar_one() == 2


def test_blocked_column_query_rejected(executor) -> None:
    ex, _ = executor
    with pytest.raises(QueryValidationError):
        ex.execute("SELECT email FROM customers")
