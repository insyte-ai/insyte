"""Unit tests for the profiler: pure column profiling and the sampling flow (fake DB)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from insyte.config.models import ProfilingSection
from insyte.connectors.base import ConnectionCheckResult, DatabaseConnector
from insyte.metadata.models import (
    CardinalityCategory,
    ScannedColumn,
    ScannedTable,
    ScanResult,
    TableCategory,
    TableKind,
)
from insyte.metadata.profiler import Profiler, build_column_profile
from insyte.metadata.repository import MetadataRepository


def test_build_profile_basic() -> None:
    profile = build_column_profile(
        "public",
        "orders",
        "status",
        "text",
        ["completed", "pending", "completed", None],
        4,
        detect_pii=True,
    )
    assert profile.null_fraction == 0.25
    assert profile.distinct_estimate == 2
    assert profile.duplicate_ratio > 0  # a repeated value → some duplication
    assert ("completed", 2) in profile.top_values
    assert profile.is_pii is False


def test_build_profile_numeric_avg() -> None:
    profile = build_column_profile(
        "public",
        "orders",
        "total",
        "numeric",
        [Decimal("10"), Decimal("20"), Decimal("30")],
        3,
        detect_pii=True,
    )
    assert profile.avg_value == 20.0
    assert profile.min_value == "10"
    assert profile.max_value == "30"


def test_build_profile_masks_pii() -> None:
    profile = build_column_profile(
        "public",
        "customers",
        "email",
        "text",
        ["alice@example.com", "bob@example.com"],
        2,
        detect_pii=True,
    )
    assert profile.is_pii is True
    assert profile.pii_type == "email"
    # No masked top value or extreme reveals the full address.
    assert all("example" not in value for value, _ in profile.top_values)
    assert profile.avg_value is None


def test_build_profile_unique_cardinality() -> None:
    profile = build_column_profile(
        "public", "orders", "id", "integer", [1, 2, 3], 3, detect_pii=True
    )
    assert profile.cardinality is CardinalityCategory.unique


# --- sampling flow against a fake connection -------------------------------------------------


class _FakeResult:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeConn:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def execute(self, _statement: object) -> _FakeResult:
        return _FakeResult(self._rows)


class FakeConnector(DatabaseConnector):
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    @property
    def host(self) -> str | None:
        return None

    @property
    def port(self) -> int | None:
        return None

    def check_connection(self) -> ConnectionCheckResult:  # pragma: no cover
        raise NotImplementedError

    @contextmanager
    def read_only_transaction(self) -> Iterator[_FakeConn]:  # type: ignore[override]
        yield _FakeConn(self._rows)

    def dispose(self) -> None:
        pass


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
                    name="customers",
                    kind=TableKind.table,
                    columns=[
                        ScannedColumn("id", 0, "integer", nullable=False, is_primary_key=True),
                        ScannedColumn("email", 1, "text", nullable=True),
                        ScannedColumn("city", 2, "text", nullable=True),
                    ],
                    primary_key_columns=["id"],
                    category=TableCategory.dimension,
                    category_confidence=0.75,
                )
            ],
            relationships=[],
        ),
        started_at=now,
        finished_at=now,
    )
    yield repo
    repo.dispose()


def test_profile_flow_masks_pii(metadata: MetadataRepository) -> None:
    rows = [(1, "alice@example.com", "Bengaluru"), (2, "bob@example.com", "Mumbai")]
    profiler = Profiler(FakeConnector(rows), metadata, ProfilingSection(sample_rows=100))
    result = profiler.profile()

    assert len(result.table_profiles) == 1
    by_column = {c.column: c for c in result.column_profiles}
    assert by_column["email"].is_pii is True
    assert all("example" not in v for v, _ in by_column["email"].top_values)
    assert by_column["city"].is_pii is False
    assert by_column["id"].cardinality is CardinalityCategory.unique
