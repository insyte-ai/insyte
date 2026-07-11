"""Compute current/previous period pairs for a time grain (for comparisons)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from insyte.analytics.models import Period, TimeGrain


def periods_for_grain(grain: TimeGrain, *, now: datetime | None = None) -> tuple[Period, Period]:
    """Return ``(current, previous)`` periods for the grain, relative to ``now`` (or UTC now)."""

    now = now or datetime.now(UTC)
    if grain is TimeGrain.day:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return _pair(start, timedelta(days=1), "%Y-%m-%d")
    if grain is TimeGrain.week:
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start = midnight - timedelta(days=midnight.weekday())
        return _pair(start, timedelta(weeks=1), "%Y-%m-%d")
    if grain is TimeGrain.year:
        start = datetime(now.year, 1, 1, tzinfo=UTC)
        return (
            Period(str(now.year), start, datetime(now.year + 1, 1, 1, tzinfo=UTC)),
            Period(str(now.year - 1), datetime(now.year - 1, 1, 1, tzinfo=UTC), start),
        )
    months = 3 if grain is TimeGrain.quarter else 1
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    end = _add_months(start, months)
    previous = _add_months(start, -months)
    return (
        Period(start.strftime("%b %Y"), start, end),
        Period(previous.strftime("%b %Y"), previous, start),
    )


def _pair(start: datetime, span: timedelta, fmt: str) -> tuple[Period, Period]:
    return (
        Period(start.strftime(fmt), start, start + span),
        Period((start - span).strftime(fmt), start - span, start),
    )


def _add_months(value: datetime, months: int) -> datetime:
    index = value.month - 1 + months
    return datetime(value.year + index // 12, index % 12 + 1, 1, tzinfo=UTC)
