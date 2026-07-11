"""Translate relative-period tokens (e.g. ``last_month``) into concrete :class:`Period` ranges.

The set of tokens is closed: the LLM may only choose one of these, and the actual date maths
happens here in Python — the model is never trusted to compute dates.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from insyte.analytics.models import Period

# The only period tokens the resolver accepts. ``all_time`` (and ``None``) mean "no filter".
RELATIVE_PERIODS: tuple[str, ...] = (
    "today",
    "yesterday",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "this_quarter",
    "last_quarter",
    "this_year",
    "last_year",
    "last_7_days",
    "last_30_days",
    "last_90_days",
    "last_12_months",
    "all_time",
)


def _midnight(value: datetime) -> datetime:
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _add_months(value: datetime, months: int) -> datetime:
    index = value.month - 1 + months
    return datetime(value.year + index // 12, index % 12 + 1, 1, tzinfo=UTC)


def period_from_token(token: str | None, *, now: datetime | None = None) -> Period | None:
    """Return a half-open ``[start, end)`` period for a token, or ``None`` for no time filter."""

    if not token:
        return None
    token = token.strip().lower()
    if token in ("all_time", "alltime", "all", ""):
        return None
    now = now or datetime.now(UTC)
    midnight = _midnight(now)

    if token == "today":
        return Period("today", midnight, midnight + timedelta(days=1))
    if token == "yesterday":
        return Period("yesterday", midnight - timedelta(days=1), midnight)
    if token == "this_week":
        start = midnight - timedelta(days=midnight.weekday())
        return Period("this week", start, start + timedelta(weeks=1))
    if token == "last_week":
        start = midnight - timedelta(days=midnight.weekday()) - timedelta(weeks=1)
        return Period("last week", start, start + timedelta(weeks=1))
    if token == "this_month":
        start = datetime(now.year, now.month, 1, tzinfo=UTC)
        return Period(start.strftime("%b %Y"), start, _add_months(start, 1))
    if token == "last_month":
        this_start = datetime(now.year, now.month, 1, tzinfo=UTC)
        start = _add_months(this_start, -1)
        return Period(start.strftime("%b %Y"), start, this_start)
    if token in ("this_quarter", "last_quarter"):
        q_first_month = ((now.month - 1) // 3) * 3 + 1
        this_start = datetime(now.year, q_first_month, 1, tzinfo=UTC)
        if token == "this_quarter":
            return Period("this quarter", this_start, _add_months(this_start, 3))
        start = _add_months(this_start, -3)
        return Period("last quarter", start, this_start)
    if token == "this_year":
        start = datetime(now.year, 1, 1, tzinfo=UTC)
        return Period(str(now.year), start, datetime(now.year + 1, 1, 1, tzinfo=UTC))
    if token == "last_year":
        start = datetime(now.year - 1, 1, 1, tzinfo=UTC)
        return Period(str(now.year - 1), start, datetime(now.year, 1, 1, tzinfo=UTC))
    if token == "last_7_days":
        return Period("last 7 days", midnight - timedelta(days=6), midnight + timedelta(days=1))
    if token == "last_30_days":
        return Period("last 30 days", midnight - timedelta(days=29), midnight + timedelta(days=1))
    if token == "last_90_days":
        return Period("last 90 days", midnight - timedelta(days=89), midnight + timedelta(days=1))
    if token == "last_12_months":
        start = _add_months(datetime(now.year, now.month, 1, tzinfo=UTC), -11)
        return Period("last 12 months", start, _add_months(start, 12))
    return None
