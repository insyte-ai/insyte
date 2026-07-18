"""Tests for browser-first onboarding and release checks."""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from pathlib import Path

from sqlalchemy.engine import Connection

from insyte.config import loader
from insyte.config.models import InsyteConfig
from insyte.config.secrets import stored_database_url
from insyte.connectors.base import (
    ConnectionCheckResult,
    DatabaseConnector,
    PermissionReport,
    ServerInfo,
    SSLInfo,
)
from insyte.services.setup_service import SetupService
from insyte.services.update_service import UpdateService


class FakeConnector(DatabaseConnector):
    def __init__(self, result: ConnectionCheckResult) -> None:
        self.result = result
        self.disposed = False

    @property
    def host(self) -> str:
        return "db.example.com"

    @property
    def port(self) -> int:
        return 5432

    def check_connection(self) -> ConnectionCheckResult:
        return self.result

    def read_only_transaction(self) -> AbstractContextManager[Connection]:
        raise NotImplementedError

    def dispose(self) -> None:
        self.disposed = True


def _connection() -> ConnectionCheckResult:
    return ConnectionCheckResult(
        server=ServerInfo(
            version="PostgreSQL 16", is_postgres=True, database="analytics", user="reader"
        ),
        ssl=SSLInfo(in_use=True, protocol="TLSv1.3"),
        permissions=PermissionReport(False, False, False, False),
        read_only_enforced=True,
        statement_timeout_seconds=20,
        lock_timeout_seconds=3,
    )


def test_create_project_validates_before_storing_secret() -> None:
    connector = FakeConnector(_connection())
    seen: list[tuple[str, InsyteConfig]] = []

    def factory(url: str, config: InsyteConfig) -> DatabaseConnector:
        seen.append((url, config))
        return connector

    config, result = SetupService(factory).create_project(
        name="cloud-sales",
        database_url=" postgresql://reader:secret@db.example.com:5432/analytics ",
        schemas=["public", "reporting"],
    )

    assert result.server.user == "reader"
    assert connector.disposed is True
    assert seen[0][0] == "postgresql://reader:secret@db.example.com:5432/analytics"
    assert loader.load_config("cloud-sales").database.allowed_schemas == ["public", "reporting"]
    assert stored_database_url("cloud-sales") == seen[0][0]
    assert "secret" not in config.to_yaml_dict().get("database", {})
    assert config.ai.intent_backend == "off"


def test_update_service_reports_newer_version(tmp_path: Path) -> None:
    metadata = tmp_path / "pypi.json"
    metadata.write_text(json.dumps({"info": {"version": "99.0.0"}}), encoding="utf-8")

    result = UpdateService(metadata.as_uri()).check()

    assert result.update_available is True
    assert result.latest_version == "99.0.0"
    assert result.current_version


def test_update_service_fails_closed() -> None:
    result = UpdateService("file:///definitely/missing/insyte.json").check()

    assert result.update_available is False
    assert result.latest_version is None
    assert result.error == (
        "Could not securely connect to the update server. "
        "Check your internet connection and try again."
    )
    assert "definitely/missing" not in result.error
