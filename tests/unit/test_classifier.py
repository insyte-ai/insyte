"""Unit tests for deterministic table classification."""

from __future__ import annotations

from insyte.metadata.classifier import classify_table
from insyte.metadata.models import (
    Relationship,
    RelationshipKind,
    ScannedColumn,
    ScannedTable,
    TableCategory,
    TableKind,
)


def _col(name: str, dtype: str = "integer", *, pk: bool = False) -> ScannedColumn:
    return ScannedColumn(name=name, ordinal=0, data_type=dtype, nullable=True, is_primary_key=pk)


def _table(name: str, columns, *, pk=None, kind=TableKind.table) -> ScannedTable:
    return ScannedTable(
        schema="public", name=name, kind=kind, columns=columns, primary_key_columns=pk or []
    )


def _rel(src: str, tgt: str, cols: list[str]) -> Relationship:
    return Relationship(
        source_schema="public",
        source_table=src,
        source_columns=cols,
        target_schema="public",
        target_table=tgt,
        target_columns=["id"],
        kind=RelationshipKind.foreign_key,
        confidence=1.0,
    )


def test_fact_table() -> None:
    orders = _table(
        "orders",
        [
            _col("id", pk=True),
            _col("customer_id"),
            _col("total", "numeric"),
            _col("created_at", "timestamp"),
        ],
        pk=["id"],
    )
    outgoing = [_rel("orders", "customers", ["customer_id"])]
    category, confidence = classify_table(orders, outgoing, [])
    assert category is TableCategory.fact
    assert confidence > 0.5


def test_dimension_table() -> None:
    customers = _table("customers", [_col("id", pk=True), _col("name", "text")], pk=["id"])
    incoming = [_rel("orders", "customers", ["customer_id"])]
    category, _ = classify_table(customers, [], incoming)
    assert category is TableCategory.dimension


def test_bridge_table() -> None:
    order_items = _table(
        "order_items",
        [_col("order_id", pk=True), _col("product_id", pk=True)],
        pk=["order_id", "product_id"],
    )
    outgoing = [
        _rel("order_items", "orders", ["order_id"]),
        _rel("order_items", "products", ["product_id"]),
    ]
    category, _ = classify_table(order_items, outgoing, [])
    assert category is TableCategory.bridge


def test_view_is_unknown() -> None:
    view = _table(
        "monthly_revenue", [_col("month", "date"), _col("revenue", "numeric")], kind=TableKind.view
    )
    category, _ = classify_table(view, [], [])
    assert category is TableCategory.unknown


def test_unrelated_table_unknown() -> None:
    misc = _table("misc", [_col("id", pk=True), _col("label", "text")], pk=["id"])
    category, _ = classify_table(misc, [], [])
    assert category is TableCategory.unknown
