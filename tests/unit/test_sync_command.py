"""Unit tests for ``insyte sync`` and the analytics connector factory."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
import sqlalchemy
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection
from typer.testing import CliRunner

from insyte.cli import sync_command
from insyte.cli.app import app
from insyte.config import loader, paths
from insyte.config.models import AnalyticsMode, InsyteConfig, ProjectSection
from insyte.connectors.base import ConnectionCheckResult, DatabaseConnector
from insyte.connectors.duckdb import DuckDBConnector
from insyte.connectors.factory import build_analytics_connector, duckdb_path, uses_local_warehouse
from insyte.metadata.models import (
    ScannedColumn,
    ScannedTable,
    ScanResult,
    TableCategory,
    TableKind,
)
from insyte.metadata.repository import MetadataRepository

runner = CliRunner()


class SqliteConnector(DatabaseConnector):
    def __init__(self) -> None:
        self._engine: Engine = create_engine("sqlite://", poolclass=sqlalchemy.pool.StaticPool)
        with self._engine.begin() as conn:
            conn.execute(text("ATTACH DATABASE ':memory:' AS public"))
            conn.execute(text("CREATE TABLE public.orders(id int, city text, total int)"))
            conn.execute(text("INSERT INTO public.orders VALUES (1,'BLR',100),(2,'BOM',200)"))

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


def _scan(name: str) -> None:
    loader.create_project(InsyteConfig(project=ProjectSection(name=name)))
    repo = MetadataRepository(paths.metadata_path(name))
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
                        ScannedColumn("id", 0, "integer", nullable=False, is_primary_key=True),
                        ScannedColumn("city", 1, "text", nullable=True),
                        ScannedColumn("total", 2, "integer", nullable=True),
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
    repo.dispose()


@pytest.fixture
def project(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _scan("demo")
    monkeypatch.setenv("INSYTE_DATABASE_URL", "postgresql://reader:pw@localhost:5432/db")
    monkeypatch.setattr(sync_command, "_make_source_connector", lambda config: SqliteConnector())


def test_sync_table_loads_duckdb(project: None) -> None:
    result = runner.invoke(app, ["sync", "--table", "orders"])
    assert result.exit_code == 0, result.stdout
    assert "public.orders" in result.stdout

    con = duckdb.connect(str(duckdb_path(loader.load_config("demo"))), read_only=True)
    assert con.execute('SELECT count(*) FROM "public"."orders"').fetchone()[0] == 2
    con.close()

    repo = MetadataRepository(paths.metadata_path("demo"))
    assert repo.get_sync_state("public.orders") is not None
    repo.dispose()


def test_sync_status(project: None) -> None:
    runner.invoke(app, ["sync", "--table", "orders"])
    result = runner.invoke(app, ["sync", "--status"])
    assert result.exit_code == 0
    assert "public.orders" in result.stdout


def test_sync_nothing_selected(project: None) -> None:
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "Nothing synced yet" in result.stdout


def test_sync_unknown_table(project: None) -> None:
    result = runner.invoke(app, ["sync", "--table", "ghost"])
    assert result.exit_code == 1
    assert "not in the scanned metadata" in result.stdout


def test_local_query_after_sync(project: None) -> None:
    """After syncing, local mode queries the DuckDB copy — no DB credentials needed."""
    runner.invoke(app, ["sync", "--table", "orders"])
    config = loader.load_config("demo")
    config.analytics.mode = AnalyticsMode.local
    loader.save_config(config)

    assert uses_local_warehouse(config) is True
    connector = build_analytics_connector(config)
    assert isinstance(connector, DuckDBConnector)
    with connector.read_only_transaction() as conn:
        assert conn.execute(text('SELECT count(*) FROM "public"."orders"')).scalar_one() == 2
    connector.dispose()


def test_factory_direct_mode_uses_postgres(project: None) -> None:
    config = loader.load_config("demo")  # default mode is direct
    assert uses_local_warehouse(config) is False
    connector = build_analytics_connector(config)
    # Direct mode builds a PostgreSQL connector from the env URL.
    assert connector.__class__.__name__ == "PostgresConnector"
    connector.dispose()
