"""Unit tests for cursor detection, extraction, and the sync engine (real DuckDB)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection

from insyte.connectors.base import ConnectionCheckResult, DatabaseConnector
from insyte.metadata.models import (
    ColumnInfo,
    ScannedColumn,
    ScannedTable,
    ScanResult,
    TableCategory,
    TableKind,
)
from insyte.metadata.repository import MetadataRepository
from insyte.warehouse.duckdb_manager import DuckDBManager
from insyte.warehouse.extractor import Extractor
from insyte.warehouse.sync_engine import SyncEngine
from insyte.warehouse.sync_state import detect_cursor


def _col(name: str, dtype: str, *, pk: bool = False) -> ColumnInfo:
    return ColumnInfo(
        name=name,
        ordinal=0,
        data_type=dtype,
        nullable=True,
        is_primary_key=pk,
        is_unique=False,
        comment=None,
    )


def test_detect_cursor_prefers_updated_at() -> None:
    columns = [
        _col("id", "integer", pk=True),
        _col("created_at", "timestamptz"),
        _col("updated_at", "timestamptz"),
    ]
    assert detect_cursor(columns) == ("updated_at", "timestamp")


def test_detect_cursor_falls_back_to_integer_pk() -> None:
    assert detect_cursor([_col("id", "integer", pk=True), _col("name", "text")]) == (
        "id",
        "integer",
    )


def test_detect_cursor_none() -> None:
    assert detect_cursor([_col("name", "text"), _col("label", "text")]) == (None, None)


# --- sync engine against a SQLite source + real DuckDB target --------------------------------


class SqliteConnector(DatabaseConnector):
    def __init__(self) -> None:
        import sqlalchemy

        self._engine: Engine = create_engine("sqlite://", poolclass=sqlalchemy.pool.StaticPool)
        with self._engine.begin() as conn:
            # Attach a "public" schema so schema-qualified table names resolve, like PostgreSQL.
            conn.execute(text("ATTACH DATABASE ':memory:' AS public"))
            conn.execute(text("CREATE TABLE public.orders(id int, city text, total int)"))
            conn.execute(text("INSERT INTO public.orders VALUES (1,'BLR',100),(2,'BOM',200)"))

    def add_rows(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(text("INSERT INTO public.orders VALUES (3,'DEL',300),(4,'PNQ',400)"))

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
    yield repo
    repo.dispose()


def _engine(
    metadata: MetadataRepository, connector: SqliteConnector, tmp_path: Path
) -> tuple[SyncEngine, Path]:
    duckdb_path = tmp_path / "analytics.duckdb"
    manager = DuckDBManager(duckdb_path)
    extractor = Extractor(connector, tmp_path / "cache")
    return SyncEngine(metadata, extractor, manager), duckdb_path


def test_full_sync(metadata: MetadataRepository, tmp_path: Path) -> None:
    connector = SqliteConnector()
    engine, duckdb_path = _engine(metadata, connector, tmp_path)

    outcome = engine.sync_table("public", "orders", incremental=False)
    assert outcome.status == "completed"
    assert outcome.mode == "full"
    assert outcome.rows == 2
    assert outcome.cursor_column == "id"  # integer PK cursor

    con = duckdb.connect(str(duckdb_path), read_only=True)
    assert con.execute('SELECT count(*) FROM "public"."orders"').fetchone()[0] == 2
    con.close()
    connector.dispose()


def test_incremental_sync_appends(metadata: MetadataRepository, tmp_path: Path) -> None:
    connector = SqliteConnector()
    engine, duckdb_path = _engine(metadata, connector, tmp_path)

    engine.sync_table("public", "orders", incremental=False)  # full: rows 1,2
    connector.add_rows()  # adds ids 3,4
    outcome = engine.sync_table("public", "orders", incremental=True)

    assert outcome.mode == "incremental"
    assert outcome.rows == 2  # only the new rows
    assert outcome.total_rows == 4

    con = duckdb.connect(str(duckdb_path), read_only=True)
    assert con.execute('SELECT count(*) FROM "public"."orders"').fetchone()[0] == 4
    con.close()

    state = metadata.get_sync_state("public.orders")
    assert state is not None and state.row_count == 4 and state.last_cursor == "4"
    connector.dispose()


def test_sync_unknown_table(metadata: MetadataRepository, tmp_path: Path) -> None:
    connector = SqliteConnector()
    engine, _ = _engine(metadata, connector, tmp_path)
    outcome = engine.sync_table("public", "ghost", incremental=False)
    assert outcome.status == "failed"
    connector.dispose()
