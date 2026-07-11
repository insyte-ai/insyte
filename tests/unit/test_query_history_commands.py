"""Unit tests for the ``insyte query`` and ``insyte history`` commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from insyte.cli import query_command
from insyte.cli.app import app
from insyte.config import loader, paths
from insyte.config.models import DatabaseSection, InsyteConfig, ProjectSection
from insyte.exceptions import QueryValidationError
from insyte.metadata.repository import MetadataRepository
from insyte.query.models import ExecutionResult, QueryHistoryEntry, SecurityEventEntry

runner = CliRunner()


class FakeExecutor:
    def __init__(self, result: ExecutionResult | None = None, error: Exception | None = None):
        self._result = result
        self._error = error

    def execute(self, sql: str, *, source: str = "direct") -> ExecutionResult:
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


@pytest.fixture
def project(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> InsyteConfig:
    config = InsyteConfig(
        project=ProjectSection(name="demo"),
        database=DatabaseSection(url_env="INSYTE_DATABASE_URL"),
    )
    loader.create_project(config)
    monkeypatch.setenv("INSYTE_DATABASE_URL", "postgresql://reader:pw@localhost:5432/app_db")
    return config


def _patch_executor(monkeypatch: pytest.MonkeyPatch, executor: FakeExecutor) -> None:
    monkeypatch.setattr(query_command, "_make_executor", lambda cfg, rec: executor)


def test_query_success(project: InsyteConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    result = ExecutionResult(
        columns=["city", "revenue"],
        rows=[("Bengaluru", 400), ("Mumbai", 200)],
        row_count=2,
        truncated=False,
        duration_ms=12.0,
        applied_limit=500,
        normalized_sql="SELECT city, SUM(total) AS revenue FROM orders GROUP BY city LIMIT 500",
        referenced_tables=["orders"],
    )
    _patch_executor(monkeypatch, FakeExecutor(result=result))
    out = runner.invoke(app, ["query", "SELECT city, sum(total) FROM orders GROUP BY city"])
    assert out.exit_code == 0, out.stdout
    assert "Validated SQL" in out.stdout
    assert "Bengaluru" in out.stdout


def test_query_blocked(project: InsyteConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    error = QueryValidationError(["Statement type 'DROP' is not allowed in a read-only query."])
    _patch_executor(monkeypatch, FakeExecutor(error=error))
    out = runner.invoke(app, ["query", "DROP TABLE orders"])
    assert out.exit_code == 1
    assert "blocked" in out.stdout.lower()
    assert "No query was sent" in out.stdout


def test_query_direct_disabled(project: InsyteConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    config = loader.load_config("demo")
    config.query.allow_direct_query = False
    loader.save_config(config)
    out = runner.invoke(app, ["query", "SELECT 1"])
    assert out.exit_code == 1
    assert "disabled" in out.stdout.lower()


def test_history_shows_records(project: InsyteConfig) -> None:
    repo = MetadataRepository(paths.metadata_path("demo"))
    repo.record_query(
        QueryHistoryEntry(
            source="direct",
            raw_sql="SELECT 1",
            normalized_sql="SELECT 1 LIMIT 500",
            referenced_tables=[],
            status="ok",
            row_count=1,
            duration_ms=5.0,
        )
    )
    repo.record_security_event(
        SecurityEventEntry(
            source="direct",
            event_type="blocked_query",
            raw_sql="DROP TABLE orders",
            violations=["blocked"],
        )
    )
    repo.dispose()

    out = runner.invoke(app, ["history"])
    assert out.exit_code == 0
    assert "Query history" in out.stdout
    assert "Security events" in out.stdout


def test_history_empty(project: InsyteConfig) -> None:
    out = runner.invoke(app, ["history"])
    assert out.exit_code == 0
    assert "No query history" in out.stdout
