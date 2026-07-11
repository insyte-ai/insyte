"""Unit tests for ``insyte connect`` using a fake connector (no live database)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from insyte.cli import connect_command
from insyte.cli.app import app
from insyte.config import loader
from insyte.config.models import DatabaseSection, InsyteConfig, ProjectSection
from insyte.connectors.base import (
    ConnectionCheckResult,
    DatabaseConnector,
    PermissionReport,
    ServerInfo,
    SSLInfo,
)
from insyte.exceptions import DatabaseConnectionError

runner = CliRunner()


class FakeConnector(DatabaseConnector):
    def __init__(self, result: ConnectionCheckResult | None = None, error: Exception | None = None):
        self._result = result
        self._error = error
        self.disposed = False

    @property
    def host(self) -> str | None:
        return "db.internal"

    @property
    def port(self) -> int | None:
        return 5432

    def check_connection(self) -> ConnectionCheckResult:
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result

    def read_only_transaction(self):  # pragma: no cover - not used in these tests
        raise NotImplementedError

    def dispose(self) -> None:
        self.disposed = True


def _result(
    *, write_access: bool = False, ssl: bool = True, is_pg: bool = True
) -> ConnectionCheckResult:
    return ConnectionCheckResult(
        server=ServerInfo(
            version="PostgreSQL 16.2 on x86_64", is_postgres=is_pg, database="app_db", user="reader"
        ),
        ssl=SSLInfo(in_use=ssl, cipher="TLS_AES_256", protocol="TLSv1.3"),
        permissions=PermissionReport(
            is_superuser=write_access,
            can_create_db=False,
            can_create_role=False,
            has_write_privileges=False,
        ),
        read_only_enforced=True,
        statement_timeout_seconds=20,
        lock_timeout_seconds=3,
    )


@pytest.fixture
def project(isolated_home: Path) -> InsyteConfig:
    config = InsyteConfig(
        project=ProjectSection(name="demo"),
        database=DatabaseSection(url_env="INSYTE_DATABASE_URL"),
    )
    loader.create_project(config)
    return config


def _patch_connector(monkeypatch: pytest.MonkeyPatch, connector: FakeConnector) -> None:
    monkeypatch.setattr(connect_command, "_make_connector", lambda url, config: connector)


def test_connect_success(project: InsyteConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INSYTE_DATABASE_URL", "postgresql://reader:pw@db.internal:5432/app_db")
    fake = FakeConnector(result=_result())
    _patch_connector(monkeypatch, fake)

    result = runner.invoke(app, ["connect"])
    assert result.exit_code == 0, result.stdout
    assert "Connected" in result.stdout
    assert "app_db" in result.stdout
    # The password must never appear in output.
    assert "pw" not in result.stdout.replace("Password", "")
    assert fake.disposed is True


def test_connect_missing_env_var(project: InsyteConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSYTE_DATABASE_URL", raising=False)
    result = runner.invoke(app, ["connect"])
    assert result.exit_code == 1
    assert "No database URL" in result.stdout


def test_connect_no_project(isolated_home: Path) -> None:
    result = runner.invoke(app, ["connect"])
    assert result.exit_code == 1
    assert "No Insyte projects" in result.stdout


def test_connect_connection_error_renders_help(
    project: InsyteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INSYTE_DATABASE_URL", "postgresql://reader:pw@db.internal:5432/app_db")
    error = DatabaseConnectionError("db.internal", 5432, "could not translate host name")
    _patch_connector(monkeypatch, FakeConnector(error=error))

    result = runner.invoke(app, ["connect"])
    assert result.exit_code == 1
    assert "Unable to connect to PostgreSQL" in result.stdout
    assert "insyte doctor" in result.stdout
    assert "pw" not in result.stdout


def test_connect_write_permission_warning_with_yes(
    project: InsyteConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INSYTE_DATABASE_URL", "postgresql://reader:pw@db.internal:5432/app_db")
    _patch_connector(monkeypatch, FakeConnector(result=_result(write_access=True)))

    result = runner.invoke(app, ["connect", "--yes"])
    assert result.exit_code == 0, result.stdout
    assert "write permissions" in result.stdout
    assert "Connection validated" in result.stdout
