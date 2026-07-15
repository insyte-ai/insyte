"""Unit tests for the SQLite metadata repository."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from insyte.metadata.models import (
    CardinalityCategory,
    ColumnProfile,
    ProfileResult,
    Relationship,
    RelationshipKind,
    ScannedColumn,
    ScannedForeignKey,
    ScannedIndex,
    ScannedTable,
    ScanResult,
    TableCategory,
    TableKind,
    TableProfile,
)
from insyte.metadata.repository import MetadataRepository


def _sample_result() -> ScanResult:
    customers = ScannedTable(
        schema="public",
        name="customers",
        kind=TableKind.table,
        columns=[
            ScannedColumn("id", 0, "integer", nullable=False, is_primary_key=True),
            ScannedColumn("city", 1, "text", nullable=True, comment="Home city"),
        ],
        primary_key_columns=["id"],
        indexes=[ScannedIndex("customers_pkey", ["id"], is_unique=True, is_primary=True)],
        row_estimate=1000,
        size_bytes=8192,
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
        ],
        primary_key_columns=["id"],
        foreign_keys=[ScannedForeignKey("fk", ["customer_id"], "public", "customers", ["id"])],
        row_estimate=50000,
        category=TableCategory.fact,
        category_confidence=0.8,
    )
    relationship = Relationship(
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
        relationships=[relationship],
        server_version="PostgreSQL 16.2",
    )


@pytest.fixture
def repository(tmp_path: Path) -> MetadataRepository:
    repo = MetadataRepository(tmp_path / "metadata.sqlite")
    yield repo
    repo.dispose()


def _save(repo: MetadataRepository) -> None:
    now = datetime.now(UTC)
    repo.save_scan(_sample_result(), started_at=now, finished_at=now)


def test_save_and_summary(repository: MetadataRepository) -> None:
    now = datetime.now(UTC)
    summary = repository.save_scan(_sample_result(), started_at=now, finished_at=now)
    assert summary.table_count == 2
    assert summary.column_count == 4
    assert summary.relationship_count == 1
    assert summary.server_version == "PostgreSQL 16.2"


def test_list_tables(repository: MetadataRepository) -> None:
    _save(repository)
    tables = repository.list_tables()
    names = [t.name for t in tables]
    assert names == ["customers", "orders"]
    assert repository.list_schemas() == ["public"]


def test_get_table_detail(repository: MetadataRepository) -> None:
    _save(repository)
    detail = repository.get_table(None, "orders")
    assert detail is not None
    assert detail.summary.category == "fact"
    assert [c.name for c in detail.columns] == ["id", "customer_id"]
    assert len(detail.outgoing) == 1
    assert detail.outgoing[0].target_table == "customers"

    customers = repository.get_table("public", "customers")
    assert customers is not None
    assert len(customers.incoming) == 1  # referenced by orders
    assert customers.indexes[0].columns == ["id"]


def test_list_table_details_bulk_loads_same_metadata(repository: MetadataRepository) -> None:
    _save(repository)
    details = repository.list_table_details()
    assert [detail.summary.name for detail in details] == ["customers", "orders"]
    assert [column.name for column in details[0].columns] == ["id", "city"]
    assert len(details[0].incoming) == 1
    assert len(details[1].outgoing) == 1
    assert [detail.summary.name for detail in repository.list_table_details(1)] == ["customers"]


def test_get_missing_table(repository: MetadataRepository) -> None:
    _save(repository)
    assert repository.get_table(None, "ghost") is None


def test_rescan_replaces_structural_data(repository: MetadataRepository) -> None:
    _save(repository)
    # Re-scan with a single table; the old ones must be gone (not accumulated).
    now = datetime.now(UTC)
    smaller = ScanResult(
        schemas={"public": None},
        tables=[
            ScannedTable(
                schema="public",
                name="products",
                kind=TableKind.table,
                columns=[ScannedColumn("id", 0, "integer", nullable=False, is_primary_key=True)],
                primary_key_columns=["id"],
            )
        ],
        relationships=[],
    )
    repository.save_scan(smaller, started_at=now, finished_at=now)
    assert [t.name for t in repository.list_tables()] == ["products"]
    assert repository.list_relationships() == []


def test_rescan_invalidates_profiles_only_when_schema_changes(
    repository: MetadataRepository,
) -> None:
    _save(repository)
    repository.save_profiles(
        ProfileResult(
            table_profiles=[TableProfile("public", "customers", 1000, 10, 2)],
            column_profiles=[
                ColumnProfile(
                    "public",
                    "customers",
                    "city",
                    0.0,
                    2,
                    0.8,
                    CardinalityCategory.low,
                    10,
                )
            ],
        )
    )
    assert repository.has_profiles()

    _save(repository)
    assert repository.has_profiles()

    changed = _sample_result()
    changed.tables[0].columns.append(ScannedColumn("country", 2, "text", nullable=True))
    now = datetime.now(UTC)
    repository.save_scan(changed, started_at=now, finished_at=now)
    assert not repository.has_profiles()
    assert repository.list_column_profiles() == []


def test_search_documents_uses_comments_and_column_names(
    repository: MetadataRepository,
) -> None:
    _save(repository)
    comments = repository.search_documents("home")
    assert comments and comments[0]["object_id"] == "public.customers.city"
    columns = repository.search_documents("customer id")
    assert any(item["object_id"] == "public.orders.customer_id" for item in columns)


def test_latest_scan(repository: MetadataRepository) -> None:
    assert repository.latest_scan() is None
    _save(repository)
    latest = repository.latest_scan()
    assert latest is not None
    assert latest.table_count == 2
