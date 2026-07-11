"""A read-only connector over the local DuckDB analytical copy.

Used when ``analytics.mode`` is ``local``: analytical queries run against the synced DuckDB
file instead of PostgreSQL. Because the data is local, this path needs no database credentials
at all. The same validated, postgres-dialect SQL runs here (DuckDB is highly compatible).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from insyte.connectors.base import (
    ConnectionCheckResult,
    DatabaseConnector,
    PermissionReport,
    ServerInfo,
    SSLInfo,
)


class DuckDBConnector(DatabaseConnector):
    """Read-only connector backed by a local DuckDB file."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._engine: Engine | None = None

    @property
    def host(self) -> str | None:
        return None

    @property
    def port(self) -> int | None:
        return None

    def _engine_or_create(self) -> Engine:
        if self._engine is None:
            self._engine = create_engine(
                f"duckdb:///{self._path}", connect_args={"read_only": True}
            )
        return self._engine

    @contextmanager
    def read_only_transaction(self) -> Iterator[Connection]:
        engine = self._engine_or_create()
        with engine.connect() as connection:
            yield connection

    def check_connection(self) -> ConnectionCheckResult:
        with self.read_only_transaction() as conn:
            version = str(conn.execute(text("SELECT version()")).scalar_one())
        return ConnectionCheckResult(
            server=ServerInfo(
                version=f"DuckDB {version}",
                is_postgres=False,
                database=str(self._path.name),
                user="local",
            ),
            ssl=SSLInfo(in_use=False),
            permissions=PermissionReport(False, False, False, False),
            read_only_enforced=True,
            statement_timeout_seconds=0,
            lock_timeout_seconds=0,
        )

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
