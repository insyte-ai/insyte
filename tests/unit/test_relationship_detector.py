"""Unit tests for relationship detection (declared FKs + inference)."""

from __future__ import annotations

from insyte.metadata.models import (
    RelationshipKind,
    ScannedColumn,
    ScannedForeignKey,
    ScannedTable,
    TableKind,
)
from insyte.metadata.relationship_detector import (
    candidate_target_names,
    detect_relationships,
    types_compatible,
)


def _col(
    name: str, dtype: str = "integer", *, pk: bool = False, unique: bool = False
) -> ScannedColumn:
    return ScannedColumn(
        name=name, ordinal=0, data_type=dtype, nullable=True, is_primary_key=pk, is_unique=unique
    )


def _table(
    name: str, columns: list[ScannedColumn], *, pk: list[str] | None = None, fks=None
) -> ScannedTable:
    return ScannedTable(
        schema="public",
        name=name,
        kind=TableKind.table,
        columns=columns,
        primary_key_columns=pk or [],
        foreign_keys=fks or [],
    )


def test_types_compatible_int_family() -> None:
    assert types_compatible("integer", "bigint")
    assert types_compatible("INTEGER", "int4")
    assert not types_compatible("integer", "uuid")


def test_candidate_names_pluralisation() -> None:
    assert "customers" in candidate_target_names("customer")
    assert "cities" in candidate_target_names("city")


def test_declared_foreign_key_is_certain() -> None:
    orders = _table(
        "orders",
        [_col("id", pk=True), _col("customer_id")],
        pk=["id"],
        fks=[
            ScannedForeignKey(
                name="orders_customer_fk",
                columns=["customer_id"],
                target_schema="public",
                target_table="customers",
                target_columns=["id"],
            )
        ],
    )
    customers = _table("customers", [_col("id", pk=True)], pk=["id"])

    rels = detect_relationships([orders, customers])
    assert len(rels) == 1
    assert rels[0].kind is RelationshipKind.foreign_key
    assert rels[0].confidence == 1.0
    assert rels[0].constraint_name == "orders_customer_fk"


def test_inferred_relationship_by_naming() -> None:
    orders = _table("orders", [_col("id", pk=True), _col("customer_id")], pk=["id"])
    customers = _table("customers", [_col("id", pk=True)], pk=["id"])

    rels = detect_relationships([orders, customers])
    inferred = [r for r in rels if r.kind is RelationshipKind.inferred]
    assert len(inferred) == 1
    rel = inferred[0]
    assert rel.source_table == "orders"
    assert rel.source_columns == ["customer_id"]
    assert rel.target_table == "customers"
    assert rel.target_columns == ["id"]
    assert 0.5 < rel.confidence < 1.0  # inferred, never certain


def test_declared_fk_not_duplicated_by_inference() -> None:
    orders = _table(
        "orders",
        [_col("id", pk=True), _col("customer_id")],
        pk=["id"],
        fks=[
            ScannedForeignKey(
                name="fk",
                columns=["customer_id"],
                target_schema="public",
                target_table="customers",
                target_columns=["id"],
            )
        ],
    )
    customers = _table("customers", [_col("id", pk=True)], pk=["id"])
    rels = detect_relationships([orders, customers])
    assert len(rels) == 1  # only the declared FK, no duplicate inference


def test_no_inference_without_matching_table() -> None:
    orders = _table("orders", [_col("id", pk=True), _col("widget_id")], pk=["id"])
    rels = detect_relationships([orders])
    assert rels == []


def test_no_inference_on_type_mismatch() -> None:
    orders = _table("orders", [_col("id", pk=True), _col("customer_id", "uuid")], pk=["id"])
    customers = _table("customers", [_col("id", "integer", pk=True)], pk=["id"])
    rels = detect_relationships([orders, customers])
    assert [r for r in rels if r.kind is RelationshipKind.inferred] == []
