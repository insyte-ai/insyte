"""Unit tests for semantic-layer generation and validation."""

from __future__ import annotations

from insyte.metadata.models import (
    CardinalityCategory,
    ColumnInfo,
    ColumnProfile,
    TableDetail,
    TableSummary,
)
from insyte.semantic.generator import generate_semantic
from insyte.semantic.models import Metric, MetricStatus, SemanticLayer
from insyte.semantic.validator import SchemaIndex, validate_semantic


def _col(name: str, dtype: str, *, pk: bool = False) -> ColumnInfo:
    return ColumnInfo(
        name=name,
        ordinal=0,
        data_type=dtype,
        nullable=True,
        is_primary_key=pk,
        is_unique=False,
        comment=None,
    )


def _detail(
    name: str, category: str, columns: list[ColumnInfo], *, kind: str = "table"
) -> TableDetail:
    return TableDetail(
        summary=TableSummary(
            schema="public",
            name=name,
            kind=kind,
            row_estimate=100,
            size_bytes=None,
            column_count=len(columns),
            category=category,
            category_confidence=0.8,
        ),
        columns=columns,
        indexes=[],
        outgoing=[],
        incoming=[],
    )


def test_generate_metrics_and_dimensions() -> None:
    orders = _detail(
        "orders",
        "fact",
        [
            _col("id", "integer", pk=True),
            _col("customer_id", "integer"),
            _col("status", "text"),
            _col("total_amount", "numeric"),
            _col("created_at", "timestamptz"),
        ],
    )
    result = generate_semantic([orders], profiles={}, existing=SemanticLayer())

    assert "order" in result.layer.entities
    assert "order_count" in result.layer.metrics
    assert "total_amount" in result.layer.metrics
    # total_amount → currency format via the name hint.
    assert result.layer.metrics["total_amount"].format.value == "currency"
    assert "status" in result.layer.dimensions
    assert all(m.status is MetricStatus.suggested for m in result.layer.metrics.values())


def test_generate_metrics_from_analysis_ready_view() -> None:
    circulation = _detail(
        "branch_circulation_analysis",
        "unknown",
        [
            _col("branch_id", "integer"),
            _col("branch_name", "text"),
            _col("genre", "text"),
            _col("borrow_count", "integer"),
            _col("late_return_rate", "numeric"),
            _col("avg_days_on_loan", "numeric"),
        ],
        kind="view",
    )

    result = generate_semantic([circulation], profiles={}, existing=SemanticLayer())

    assert "total_borrow_count" in result.layer.metrics
    assert result.layer.metrics["total_borrow_count"].expression == (
        "SUM(branch_circulation_analysis.borrow_count)"
    )
    assert "avg_late_return_rate" in result.layer.metrics
    assert result.layer.metrics["avg_late_return_rate"].expression == (
        "AVG(branch_circulation_analysis.late_return_rate)"
    )
    assert result.layer.metrics["avg_late_return_rate"].format.value == "percent"
    assert "branch_id" not in result.layer.metrics
    assert "branch_name" in result.layer.dimensions


def test_pii_column_excluded_from_metrics_and_dimensions() -> None:
    customers = _detail(
        "customers",
        "fact",
        [_col("id", "integer", pk=True), _col("ssn", "text"), _col("balance", "numeric")],
    )
    profiles = {
        "public.customers.ssn": ColumnProfile(
            "public",
            "customers",
            "ssn",
            0.0,
            100,
            0.0,
            CardinalityCategory.high,
            100,
            is_pii=True,
            pii_type="ssn",
        )
    }
    result = generate_semantic([customers], profiles=profiles, existing=SemanticLayer())
    assert "ssn" not in result.layer.dimensions  # PII never becomes a dimension


def test_generate_preserves_existing() -> None:
    orders = _detail("orders", "fact", [_col("id", "integer", pk=True), _col("total", "numeric")])
    existing = SemanticLayer(
        metrics={
            "total_total": Metric(
                label="Custom",
                expression="SUM(x)",
                source_table="public.orders",
                status=MetricStatus.confirmed,
            )
        }
    )
    result = generate_semantic([orders], profiles={}, existing=existing)
    # The existing confirmed metric is untouched.
    assert result.layer.metrics["total_total"].label == "Custom"
    assert result.layer.metrics["total_total"].status is MetricStatus.confirmed


def test_validate_flags_bad_references() -> None:
    index = SchemaIndex(
        tables={"public.orders"},
        columns_by_qualified={"public.orders": {"id", "total_amount"}},
        columns_by_table={"orders": {"id", "total_amount"}},
    )
    layer = SemanticLayer(
        metrics={
            "good": Metric(
                label="Good", expression="SUM(orders.total_amount)", source_table="public.orders"
            ),
            "bad_table": Metric(label="Bad", expression="COUNT(*)", source_table="public.ghost"),
            "bad_expr": Metric(label="Bad", expression="SUM(", source_table="public.orders"),
        }
    )
    issues = validate_semantic(layer, index)
    targets = {(i.target, i.level) for i in issues}
    assert ("metric.bad_table", "error") in targets
    assert ("metric.bad_expr", "error") in targets
    assert not any(i.target == "metric.good" for i in issues)


def test_validate_clean_layer() -> None:
    index = SchemaIndex(
        tables={"public.orders"},
        columns_by_qualified={"public.orders": {"id", "total"}},
        columns_by_table={"orders": {"id", "total"}},
    )
    layer = SemanticLayer(
        metrics={
            "m": Metric(label="M", expression="SUM(orders.total)", source_table="public.orders")
        }
    )
    assert validate_semantic(layer, index) == []


def test_singularize_and_label_helpers() -> None:
    from insyte.semantic.generator import _humanize, _singularize

    assert _singularize("addresses") == "address"  # not "addresse"
    assert _singularize("categories") == "category"
    assert _singularize("boxes") == "box"
    assert _singularize("orders") == "order"
    assert _singularize("class") == "class"  # "ss" ending is left alone
    assert _humanize("grand_total") == "Grand total"
    assert _humanize("address_count") == "Address count"
