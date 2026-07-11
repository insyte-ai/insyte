"""Unit tests for analytical SQL generation and join-path finding."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from insyte.exceptions import AnalysisError, JoinPathError
from insyte.metadata.models import RelationshipInfo
from insyte.query.generator import aggregate_sql, segment_sql, timeseries_sql
from insyte.semantic.models import Dimension, Metric


def _revenue() -> Metric:
    return Metric(
        label="Revenue",
        expression="SUM(orders.total_amount)",
        source_table="public.orders",
        filters={"orders.status": ["completed"]},
        time_column="orders.completed_at",
    )


def _rel(src: str, src_col: str, tgt: str, tgt_col: str = "id") -> RelationshipInfo:
    return RelationshipInfo(
        "public", src, [src_col], "public", tgt, [tgt_col], "foreign_key", 1.0, None
    )


def test_aggregate_sql() -> None:
    sql = aggregate_sql(_revenue())
    assert "SUM(orders.total_amount) AS value" in sql
    assert "FROM public.orders" in sql
    assert "orders.status = 'completed'" in sql


def test_timeseries_sql_buckets_and_orders() -> None:
    sql = timeseries_sql(_revenue(), "week")
    assert "DATE_TRUNC('week'" in sql.replace("WEEK", "week")
    assert "GROUP BY 1" in sql
    assert "ORDER BY 1" in sql


def test_timeseries_requires_time_column() -> None:
    metric = Metric(label="X", expression="COUNT(*)", source_table="public.t")
    with pytest.raises(AnalysisError):
        timeseries_sql(metric, "week")


def test_segment_direct_join() -> None:
    dimension = Dimension(source="customers.city")
    rels = [_rel("orders", "customer_id", "customers")]
    sql = segment_sql(_revenue(), dimension, rels)
    assert "customers.city AS segment" in sql
    assert "JOIN public.customers ON orders.customer_id = customers.id" in sql
    assert "ORDER BY value DESC" in sql


def test_segment_two_hop_join() -> None:
    dimension = Dimension(source="cities.name")
    rels = [_rel("orders", "customer_id", "customers"), _rel("customers", "city_id", "cities")]
    sql = segment_sql(_revenue(), dimension, rels)
    assert "JOIN public.customers ON orders.customer_id = customers.id" in sql
    assert "JOIN public.cities ON customers.city_id = cities.id" in sql


def test_segment_no_join_needed() -> None:
    dimension = Dimension(source="orders.status")
    sql = segment_sql(_revenue(), dimension, [])
    assert "JOIN" not in sql


def test_segment_no_path_raises() -> None:
    dimension = Dimension(source="cities.name")
    with pytest.raises(JoinPathError):
        segment_sql(_revenue(), dimension, [])  # no relationships to reach cities


def test_period_bounds_applied() -> None:
    start = datetime(2026, 6, 1, tzinfo=UTC)
    end = datetime(2026, 7, 1, tzinfo=UTC)
    sql = aggregate_sql(_revenue(), start, end)
    assert "orders.completed_at >=" in sql
    assert "orders.completed_at <" in sql
