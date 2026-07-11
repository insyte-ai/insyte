"""Integration tests against a real PostgreSQL server.

These run only when ``INSYTE_TEST_DATABASE_URL`` is set (e.g. a throwaway local database or a
Testcontainers instance); otherwise they are skipped so the unit suite stays hermetic.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import InternalError, ProgrammingError

from insyte.config.models import DatabaseSection, QuerySection, SSLMode
from insyte.connectors.postgres import PostgresConnector

_TEST_URL = os.environ.get("INSYTE_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_URL, reason="Set INSYTE_TEST_DATABASE_URL to run PostgreSQL integration tests."
)


@pytest.fixture
def connector() -> PostgresConnector:
    assert _TEST_URL is not None
    conn = PostgresConnector(
        _TEST_URL,
        DatabaseSection(ssl_mode=SSLMode.prefer),
        QuerySection(),
    )
    yield conn
    conn.dispose()


def test_check_connection_reports_postgres(connector: PostgresConnector) -> None:
    result = connector.check_connection()
    assert result.server.is_postgres is True
    assert result.read_only_enforced is True
    assert result.server.database


def test_read_only_transaction_blocks_writes(connector: PostgresConnector) -> None:
    # A write inside the read-only transaction must be rejected by PostgreSQL itself.
    with (
        pytest.raises((InternalError, ProgrammingError)),
        connector.read_only_transaction() as conn,
    ):
        conn.execute(text("CREATE TEMP TABLE insyte_should_fail (id int)"))


def test_read_only_transaction_allows_select(connector: PostgresConnector) -> None:
    with connector.read_only_transaction() as conn:
        assert conn.execute(text("SELECT 1")).scalar_one() == 1
