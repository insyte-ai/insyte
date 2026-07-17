"""Regression tests for material qualifier detection."""

from insyte.semantic.models import Metric, SemanticLayer
from insyte.semantic.qualifiers import unresolved_terms


def test_this_week_does_not_create_false_this_qualifier() -> None:
    layer = SemanticLayer(
        metrics={
            "return_count": Metric(
                label="Return count",
                expression="COUNT(*)",
                source_table="public.returns",
                time_column="returns.requested_at",
            )
        }
    )

    assert (
        unresolved_terms(
            "Why has Return count increased this week? Answer please.",
            "return_count",
            layer,
        )
        == []
    )


def test_transaction_quantity_represents_products_sold() -> None:
    layer = SemanticLayer(
        metrics={
            "total_quantity": Metric(
                label="Total quantity",
                expression="SUM(order_items.quantity)",
                source_table="public.order_items",
            )
        }
    )

    assert (
        unresolved_terms("analyze products sold in the last six months", "total_quantity", layer)
        == []
    )
