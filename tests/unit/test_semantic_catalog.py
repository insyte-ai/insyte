"""Tests for fail-closed semantic retrieval and metric capabilities."""

from __future__ import annotations

from insyte.metadata.models import (
    CardinalityCategory,
    ColumnProfile,
    RelationshipInfo,
)
from insyte.semantic.catalog import SemanticCatalog
from insyte.semantic.models import Dimension, Metric, SemanticAlias, SemanticLayer


def _layer() -> SemanticLayer:
    return SemanticLayer(
        metrics={
            "revenue": Metric(
                label="Completed revenue",
                expression="SUM(orders.total_amount)",
                source_table="public.orders",
                time_column="orders.created_at",
            ),
            "inventory_units": Metric(
                label="Inventory units",
                expression="SUM(inventory.quantity)",
                source_table="public.inventory",
            ),
        },
        dimensions={
            "status": Dimension(source="orders.status", label="Order status"),
            "city": Dimension(source="customers.city", label="Customer city"),
            "customer_id": Dimension(source="orders.customer_id", label="Customer ID"),
        },
        aliases={
            "gmv": SemanticAlias(
                target="revenue",
                target_type="metric",
                confidence=0.95,
                evidence=["business terminology"],
            )
        },
    )


def test_catalog_narrows_alias_question_to_known_metric() -> None:
    narrowed, candidates = SemanticCatalog(_layer()).narrowed_layer("show monthly gmv")
    assert set(narrowed.metrics) == {"revenue"}
    assert "gmv" in narrowed.aliases
    assert any(item.name == "revenue" and item.matched_by == ("alias:gmv",) for item in candidates)


def test_catalog_keeps_full_layer_when_no_metric_has_retrieval_signal() -> None:
    layer = _layer()
    narrowed, _ = SemanticCatalog(layer).narrowed_layer("what changed unexpectedly")
    assert set(narrowed.metrics) == set(layer.metrics)


def test_catalog_maps_products_sold_to_transaction_quantity() -> None:
    layer = _layer()
    layer.metrics["total_quantity"] = Metric(
        label="Total quantity",
        expression="SUM(order_items.quantity)",
        source_table="public.order_items",
    )

    narrowed, candidates = SemanticCatalog(layer).narrowed_layer(
        "analyze products sold in last 6 months"
    )

    assert "total_quantity" in narrowed.metrics
    assert any(
        item.name == "total_quantity" and item.matched_by == ("semantic:sales_quantity",)
        for item in candidates
    )


def test_capability_uses_joins_cardinality_and_time_profile() -> None:
    profiles = [
        ColumnProfile(
            "public",
            "orders",
            "status",
            0.0,
            4,
            0.9,
            CardinalityCategory.low,
            100,
        ),
        ColumnProfile(
            "public",
            "orders",
            "customer_id",
            0.0,
            100,
            0.0,
            CardinalityCategory.unique,
            100,
        ),
        ColumnProfile(
            "public",
            "orders",
            "created_at",
            0.0,
            100,
            0.0,
            CardinalityCategory.unique,
            100,
            min_value="2025-01-01T00:00:00+00:00",
            max_value="2026-03-31T00:00:00+00:00",
        ),
        ColumnProfile(
            "public",
            "customers",
            "city",
            0.0,
            8,
            0.8,
            CardinalityCategory.low,
            100,
        ),
    ]
    relationship = RelationshipInfo(
        "public",
        "orders",
        ["customer_id"],
        "public",
        "customers",
        ["id"],
        "foreign_key",
        1.0,
        "orders_customer_fk",
    )
    capability = SemanticCatalog(
        _layer(), profiles=profiles, relationships=[relationship]
    ).capability("revenue")
    assert capability is not None
    assert capability.dimensions == ("status", "city")
    assert capability.data_start is not None and capability.data_start.year == 2025
    assert capability.data_end is not None and capability.data_end.month == 3
