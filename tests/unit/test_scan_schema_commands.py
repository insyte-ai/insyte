"""Unit tests for ``insyte scan`` and ``insyte schema`` using a fake scanner.

The scanner (the only part that needs a live database) is replaced; the real repository writes
to an isolated SQLite file, and ``insyte schema`` reads it back — exercising the full local
metadata round-trip without a database.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from insyte.cli import scan_command
from insyte.cli.app import app
from insyte.config import loader
from insyte.config.models import DatabaseSection, InsyteConfig, ProjectSection
from insyte.metadata.models import (
    Relationship,
    RelationshipKind,
    ScannedColumn,
    ScannedForeignKey,
    ScannedTable,
    ScanResult,
    TableCategory,
    TableKind,
)

runner = CliRunner()


class FakeScanner:
    def __init__(self, result: ScanResult) -> None:
        self._result = result

    def scan(self) -> ScanResult:
        return self._result


def _fixture_result() -> ScanResult:
    customers = ScannedTable(
        schema="public",
        name="customers",
        kind=TableKind.table,
        columns=[
            ScannedColumn("id", 0, "integer", nullable=False, is_primary_key=True),
            ScannedColumn("city", 1, "text", nullable=True),
        ],
        primary_key_columns=["id"],
        row_estimate=1200,
        category=TableCategory.dimension,
        category_confidence=0.75,
    )
    orders = ScannedTable(
        schema="public",
        name="orders",
        kind=TableKind.table,
        columns=[
            ScannedColumn("id", 0, "integer", nullable=False, is_primary_key=True),
            ScannedColumn("customer_id", 1, "integer", nullable=False),
            ScannedColumn("total", 2, "numeric", nullable=True),
        ],
        primary_key_columns=["id"],
        foreign_keys=[ScannedForeignKey("fk", ["customer_id"], "public", "customers", ["id"])],
        row_estimate=48000,
        category=TableCategory.fact,
        category_confidence=0.8,
    )
    rel = Relationship(
        source_schema="public",
        source_table="orders",
        source_columns=["customer_id"],
        target_schema="public",
        target_table="customers",
        target_columns=["id"],
        kind=RelationshipKind.foreign_key,
        confidence=1.0,
        constraint_name="fk",
    )
    return ScanResult(
        schemas={"public": None},
        tables=[customers, orders],
        relationships=[rel],
        server_version="PostgreSQL 16.2",
    )


@pytest.fixture
def project(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> InsyteConfig:
    config = InsyteConfig(
        project=ProjectSection(name="demo"),
        database=DatabaseSection(url_env="INSYTE_DATABASE_URL"),
    )
    loader.create_project(config)
    monkeypatch.setenv("INSYTE_DATABASE_URL", "postgresql://reader:pw@localhost:5432/app_db")
    monkeypatch.setattr(
        scan_command, "_make_scanner", lambda url, cfg: FakeScanner(_fixture_result())
    )
    return config


def test_scan_persists_and_reports(project: InsyteConfig) -> None:
    result = runner.invoke(app, ["scan"])
    assert result.exit_code == 0, result.stdout
    assert "Tables" in result.stdout
    assert "1 foreign key" in result.stdout


def test_schema_overview_after_scan(project: InsyteConfig) -> None:
    runner.invoke(app, ["scan"])
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 0, result.stdout
    assert "public.orders" in result.stdout
    assert "fact" in result.stdout
    assert "Relationship map" in result.stdout


def test_schema_table_detail(project: InsyteConfig) -> None:
    runner.invoke(app, ["scan"])
    result = runner.invoke(app, ["schema", "orders"])
    assert result.exit_code == 0, result.stdout
    assert "customer_id" in result.stdout
    assert "References (outgoing)" in result.stdout


def test_schema_table_qualified_name(project: InsyteConfig) -> None:
    runner.invoke(app, ["scan"])
    result = runner.invoke(app, ["schema", "public.customers"])
    assert result.exit_code == 0, result.stdout
    assert "Referenced by (incoming)" in result.stdout


def test_schema_missing_table(project: InsyteConfig) -> None:
    runner.invoke(app, ["scan"])
    result = runner.invoke(app, ["schema", "ghost"])
    assert result.exit_code == 1
    assert "not found" in result.stdout


def test_schema_before_scan(project: InsyteConfig) -> None:
    result = runner.invoke(app, ["schema"])
    assert result.exit_code == 1
    assert "No metadata" in result.stdout
