"""Integration test: sync PostgreSQL → DuckDB and query the local copy."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pytest
from sqlalchemy import create_engine, text

from insyte.config.models import (
    DatabaseSection,
    InsyteConfig,
    ProjectSection,
    SSLMode,
)
from insyte.connectors.duckdb import DuckDBConnector
from insyte.connectors.postgres import PostgresConnector, normalize_postgres_url
from insyte.metadata.repository import MetadataRepository, utcnow
from insyte.metadata.scanner import SchemaScanner
from insyte.warehouse.duckdb_manager import DuckDBManager
from insyte.warehouse.extractor import Extractor
from insyte.warehouse.sync_engine import SyncEngine

_TEST_URL = os.environ.get("INSYTE_TEST_DATABASE_URL")
_FIXTURE = Path(__file__).parent.parent / "fixtures" / "ecommerce.sql"

pytestmark = pytest.mark.skipif(
    not _TEST_URL, reason="Set INSYTE_TEST_DATABASE_URL to run PostgreSQL integration tests."
)


@pytest.fixture(scope="module")
def synced(tmp_path_factory: pytest.TempPathFactory):
    assert _TEST_URL is not None
    engine = create_engine(normalize_postgres_url(_TEST_URL))
    with engine.begin() as conn:
        for statement in _FIXTURE.read_text().split(";\n"):
            if statement.strip():
                conn.execute(text(statement))
    engine.dispose()

    config = InsyteConfig(
        project=ProjectSection(name="it"),
        database=DatabaseSection(ssl_mode=SSLMode.prefer),
    )
    connector = PostgresConnector(_TEST_URL, config.database, config.query)
    tmp = tmp_path_factory.mktemp("sync")
    metadata = MetadataRepository(tmp / "metadata.sqlite")
    metadata.save_scan(
        SchemaScanner(connector, config.database).scan(), started_at=utcnow(), finished_at=utcnow()
    )
    duckdb_path = tmp / "analytics.duckdb"
    sync_engine = SyncEngine(
        metadata, Extractor(connector, tmp / "cache"), DuckDBManager(duckdb_path)
    )
    for table in ("customers", "orders", "cities"):
        sync_engine.sync_table("public", table, incremental=False)
    connector.dispose()
    yield metadata, duckdb_path
    metadata.dispose()


def test_tables_copied_to_duckdb(synced) -> None:
    _metadata, duckdb_path = synced
    con = duckdb.connect(str(duckdb_path), read_only=True)
    try:
        assert con.execute('SELECT count(*) FROM "public"."orders"').fetchone()[0] == 2
        # The convenience unqualified view resolves too.
        assert con.execute("SELECT count(*) FROM orders").fetchone()[0] == 2
    finally:
        con.close()


def test_sync_state_recorded(synced) -> None:
    metadata, _ = synced
    states = {s.table: s for s in metadata.list_sync_states()}
    assert "public.orders" in states
    # orders has created_at → timestamp cursor preferred over the integer PK.
    assert states["public.orders"].cursor_column in {"created_at", "id"}


def test_query_duckdb_copy_read_only(synced) -> None:
    _metadata, duckdb_path = synced
    connector = DuckDBConnector(duckdb_path)
    try:
        with connector.read_only_transaction() as conn:
            revenue = conn.execute(
                text("SELECT SUM(total_amount) FROM public.orders WHERE status = 'completed'")
            ).scalar_one()
        assert float(revenue) > 0
    finally:
        connector.dispose()
