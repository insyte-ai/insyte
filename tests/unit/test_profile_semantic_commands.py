"""Unit tests for ``insyte profile``, ``insyte metrics approve``, ``insyte semantic``."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from insyte.cli import profile_command
from insyte.cli.app import app
from insyte.config import loader, paths
from insyte.config.models import InsyteConfig, ProjectSection
from insyte.metadata.models import (
    CardinalityCategory,
    ColumnProfile,
    ProfileResult,
    ScannedColumn,
    ScannedForeignKey,
    ScannedTable,
    ScanResult,
    TableCategory,
    TableKind,
    TableProfile,
)
from insyte.metadata.repository import MetadataRepository
from insyte.semantic.repository import SemanticRepository

runner = CliRunner()
_FIXTURE_SEMANTIC = Path(__file__).parent.parent / "fixtures" / "semantic.yaml"


def _scan_project(name: str) -> None:
    """Create a project with scanned metadata (orders + customers with a PII email column)."""
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
                        ScannedColumn("customer_id", 1, "integer", nullable=False),
                        ScannedColumn("status", 2, "text", nullable=True),
                        ScannedColumn("total_amount", 3, "numeric", nullable=True),
                        ScannedColumn("created_at", 4, "timestamptz", nullable=True),
                    ],
                    primary_key_columns=["id"],
                    foreign_keys=[
                        ScannedForeignKey("fk", ["customer_id"], "public", "customers", ["id"])
                    ],
                    category=TableCategory.fact,
                    category_confidence=0.8,
                ),
                ScannedTable(
                    schema="public",
                    name="customers",
                    kind=TableKind.table,
                    columns=[
                        ScannedColumn("id", 0, "integer", nullable=False, is_primary_key=True),
                        ScannedColumn("email", 1, "text", nullable=True),
                    ],
                    primary_key_columns=["id"],
                    category=TableCategory.dimension,
                    category_confidence=0.75,
                ),
            ],
            relationships=[],
        ),
        started_at=now,
        finished_at=now,
    )
    repo.dispose()


@pytest.fixture
def project(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _scan_project("demo")
    monkeypatch.setenv("INSYTE_DATABASE_URL", "postgresql://reader:pw@localhost:5432/db")


class FakeProfiler:
    def profile(self) -> ProfileResult:
        return ProfileResult(
            table_profiles=[TableProfile("public", "customers", 100, 100, 2)],
            column_profiles=[
                ColumnProfile(
                    "public",
                    "customers",
                    "email",
                    0.1,
                    90,
                    0.1,
                    CardinalityCategory.high,
                    100,
                    top_values=[("a***m", 1)],
                    is_pii=True,
                    pii_type="email",
                    pii_confidence=0.9,
                )
            ],
        )


def test_profile_saves_and_shows(project: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profile_command, "_make_profiler", lambda url, cfg, md: FakeProfiler())
    result = runner.invoke(app, ["profile"])
    assert result.exit_code == 0, result.stdout
    assert "possible PII" in result.stdout
    assert "email" in result.stdout
    # Profiles persisted.
    repo = MetadataRepository(paths.metadata_path("demo"))
    assert repo.has_profiles()
    repo.dispose()


def test_semantic_generate_then_validate(project: None) -> None:
    gen = runner.invoke(app, ["semantic", "generate"])
    assert gen.exit_code == 0, gen.stdout
    assert "metrics" in gen.stdout

    layer = SemanticRepository(paths.semantic_path("demo")).load()
    assert "order_count" in layer.metrics
    assert "total_amount" in layer.metrics
    assert layer.metrics["order_count"].status.value == "suggested"

    val = runner.invoke(app, ["semantic", "validate"])
    assert val.exit_code == 0, val.stdout
    assert "valid" in val.stdout.lower()


def test_metrics_list_and_approve(project: None) -> None:
    shutil.copy(_FIXTURE_SEMANTIC, paths.semantic_path("demo"))
    listing = runner.invoke(app, ["metrics"])
    assert "Metrics" in listing.stdout
    assert "suggested" in listing.stdout  # payment_failure_rate is suggested
    assert "confirmed" in listing.stdout  # completed_revenue is confirmed

    approve = runner.invoke(app, ["metrics", "approve", "payment_failure_rate"])
    assert approve.exit_code == 0, approve.stdout
    layer = SemanticRepository(paths.semantic_path("demo")).load()
    assert layer.metrics["payment_failure_rate"].status.value == "confirmed"


def test_metrics_approve_unknown(project: None) -> None:
    shutil.copy(_FIXTURE_SEMANTIC, paths.semantic_path("demo"))
    result = runner.invoke(app, ["metrics", "approve", "ghost"])
    assert result.exit_code == 1
    assert "not defined" in result.stdout


def test_semantic_validate_flags_errors(project: None) -> None:
    paths.semantic_path("demo").write_text(
        "metrics:\n  bad:\n    label: Bad\n    expression: COUNT(*)\n"
        "    source_table: public.ghost\n"
    )
    result = runner.invoke(app, ["semantic", "validate"])
    assert result.exit_code == 1
    assert "error" in result.stdout.lower()
