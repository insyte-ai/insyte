"""Tests for grounded, confirmation-required derived metric proposals."""

from __future__ import annotations

from insyte.metadata.models import CardinalityCategory, ColumnProfile
from insyte.query.generator import timeseries_sql
from insyte.semantic.models import Metric, SemanticLayer
from insyte.semantic.proposals import apply_metric_proposal, validate_metric_proposal


def _layer() -> SemanticLayer:
    return SemanticLayer(
        metrics={
            "review_count": Metric(
                label="Review count",
                expression="COUNT(*)",
                source_table="public.reviews",
                time_column="reviews.created_at",
            )
        }
    )


def _profiles(*, pii: bool = False) -> list[ColumnProfile]:
    return [
        ColumnProfile(
            schema="public",
            table="reviews",
            column="rating",
            null_fraction=0,
            distinct_estimate=5,
            duplicate_ratio=0.99,
            cardinality=CardinalityCategory.low,
            sampled_rows=1000,
            top_values=[("1", 200), ("2", 200), ("3", 200), ("4", 200), ("5", 200)],
            is_pii=pii,
        )
    ]


def _raw() -> dict:
    return {
        "name": "positive_review_count",
        "label": "Positive review count",
        "base_metric": "review_count",
        "filter_column": "reviews.rating",
        "filter_values": [4, 5],
        "aliases": ["positive feedback", "positive reviews"],
        "assumption": "Ratings 4 and 5 mean positive feedback.",
        "confidence": 0.75,
    }


def test_valid_proposal_inherits_expression_and_requires_confirmation() -> None:
    proposal = validate_metric_proposal(
        _raw(),
        _layer(),
        _profiles(),
        question="Show positive feedback and positive reviews",
    )
    assert proposal is not None

    enriched = apply_metric_proposal(proposal, _layer())
    metric = enriched.metrics["positive_review_count"]
    assert metric.expression == "COUNT(*)"
    assert metric.filters == {"reviews.rating": [4, 5]}
    assert metric.requires_confirmation is True
    assert enriched.aliases["positive feedback"].target == "positive_review_count"
    sql = timeseries_sql(metric, "month")
    assert "reviews.rating IN (4, 5)" in sql


def test_proposal_rejects_unobserved_values_and_pii() -> None:
    raw = _raw()
    raw["filter_values"] = [6]
    assert (
        validate_metric_proposal(
            raw, _layer(), _profiles(), question="positive feedback"
        )
        is None
    )
    assert (
        validate_metric_proposal(
            _raw(), _layer(), _profiles(pii=True), question="positive feedback reviews"
        )
        is None
    )


def test_proposal_rejects_cross_table_filter() -> None:
    raw = _raw()
    raw["filter_column"] = "payments.rating"
    profiles = _profiles()
    profiles[0].table = "payments"
    assert (
        validate_metric_proposal(
            raw, _layer(), profiles, question="positive feedback reviews"
        )
        is None
    )
