"""Unit tests for the safe executor.

A SQLite-backed connector provides a real read-only execution path without PostgreSQL, and a
fake recorder captures audit calls, so the full validate → execute → audit flow is exercised
hermetically.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection

from insyte.config.models import DatabaseSection, InsyteConfig, ProjectSection, QuerySection
from insyte.connectors.base import ConnectionCheckResult, DatabaseConnector
from insyte.exceptions import QueryExecutionError, QueryValidationError
from insyte.query.executor import QueryExecutor
from insyte.query.models import QueryHistoryEntry, SecurityEventEntry


class SqliteConnector(DatabaseConnector):
    """A DatabaseConnector backed by in-memory SQLite for real execution in tests."""

    def __init__(self) -> None:
        self._engine: Engine = create_engine("sqlite://")
        with self._engine.begin() as conn:
            conn.execute(text("CREATE TABLE orders (id integer, city text, total integer)"))
            conn.execute(
                text(
                    "INSERT INTO orders VALUES "
                    "(1,'Bengaluru',100),(2,'Mumbai',200),(3,'Bengaluru',300)"
                )
            )

    @property
    def host(self) -> str | None:
        return None

    @property
    def port(self) -> int | None:
        return None

    def check_connection(self) -> ConnectionCheckResult:  # pragma: no cover - unused
        raise NotImplementedError

    @contextmanager
    def read_only_transaction(self) -> Iterator[Connection]:
        with self._engine.connect() as conn:
            yield conn

    def dispose(self) -> None:
        self._engine.dispose()


class FakeRecorder:
    def __init__(self) -> None:
        self.queries: list[QueryHistoryEntry] = []
        self.events: list[SecurityEventEntry] = []

    def record_query(self, entry: QueryHistoryEntry) -> None:
        self.queries.append(entry)

    def record_security_event(self, event: SecurityEventEntry) -> None:
        self.events.append(event)


def _config(**query_kwargs: object) -> InsyteConfig:
    return InsyteConfig(
        project=ProjectSection(name="t"),
        database=DatabaseSection(allowed_schemas=["public", "main"]),
        query=QuerySection(**query_kwargs),  # type: ignore[arg-type]
    )


@pytest.fixture
def connector() -> SqliteConnector:
    conn = SqliteConnector()
    yield conn
    conn.dispose()


def test_valid_query_executes_and_audits(connector: SqliteConnector) -> None:
    recorder = FakeRecorder()
    executor = QueryExecutor(connector, _config(), recorder)

    result = executor.execute("SELECT city, sum(total) AS revenue FROM orders GROUP BY city")
    assert result.row_count == 2
    assert "city" in result.columns
    assert result.applied_limit == 500
    assert len(recorder.queries) == 1
    assert recorder.queries[0].status == "ok"
    assert recorder.events == []


def test_blocked_query_is_not_executed(connector: SqliteConnector) -> None:
    recorder = FakeRecorder()
    executor = QueryExecutor(connector, _config(), recorder)

    with pytest.raises(QueryValidationError):
        executor.execute("DROP TABLE orders")

    # The table must still exist — nothing was sent to the database.
    with connector.read_only_transaction() as conn:
        assert conn.execute(text("SELECT count(*) FROM orders")).scalar_one() == 3
    assert recorder.queries[0].status == "blocked"
    assert len(recorder.events) == 1
    assert recorder.events[0].event_type == "blocked_query"


def test_runtime_error_is_audited(connector: SqliteConnector) -> None:
    recorder = FakeRecorder()
    executor = QueryExecutor(connector, _config(), recorder)

    with pytest.raises(QueryExecutionError):
        executor.execute("SELECT * FROM nonexistent_table")
    assert recorder.queries[0].status == "error"
    assert recorder.queries[0].error


def test_result_truncated_by_byte_cap(connector: SqliteConnector) -> None:
    recorder = FakeRecorder()
    executor = QueryExecutor(connector, _config(maximum_result_bytes=5), recorder)

    result = executor.execute("SELECT * FROM orders")
    assert result.truncated is True
    assert result.row_count < 3
