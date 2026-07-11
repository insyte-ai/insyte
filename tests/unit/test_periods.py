"""Unit tests for period computation."""

from __future__ import annotations

from datetime import UTC, datetime

from insyte.analytics.models import TimeGrain
from insyte.analytics.periods import periods_for_grain

_NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def test_month_periods() -> None:
    current, previous = periods_for_grain(TimeGrain.month, now=_NOW)
    assert current.start == datetime(2026, 7, 1, tzinfo=UTC)
    assert current.end == datetime(2026, 8, 1, tzinfo=UTC)
    assert previous.start == datetime(2026, 6, 1, tzinfo=UTC)
    assert previous.end == current.start


def test_month_periods_wrap_january() -> None:
    current, previous = periods_for_grain(TimeGrain.month, now=datetime(2026, 1, 10, tzinfo=UTC))
    assert previous.start == datetime(2025, 12, 1, tzinfo=UTC)
    assert current.start == datetime(2026, 1, 1, tzinfo=UTC)


def test_year_periods() -> None:
    current, previous = periods_for_grain(TimeGrain.year, now=_NOW)
    assert current.start == datetime(2026, 1, 1, tzinfo=UTC)
    assert previous.start == datetime(2025, 1, 1, tzinfo=UTC)


def test_week_periods_span_seven_days() -> None:
    current, previous = periods_for_grain(TimeGrain.week, now=_NOW)
    assert (current.end - current.start).days == 7
    assert previous.end == current.start


def test_day_periods() -> None:
    current, previous = periods_for_grain(TimeGrain.day, now=_NOW)
    assert current.start == datetime(2026, 7, 15, tzinfo=UTC)
    assert (current.end - current.start).days == 1
    assert previous.start == datetime(2026, 7, 14, tzinfo=UTC)
