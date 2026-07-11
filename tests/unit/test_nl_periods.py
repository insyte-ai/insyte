"""Tests for relative-period token resolution."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from insyte.nl.periods import RELATIVE_PERIODS, period_from_token

# A fixed "now": Wednesday, 15 July 2026, 10:30 UTC.
NOW = datetime(2026, 7, 15, 10, 30, tzinfo=UTC)


def test_none_and_all_time_mean_no_filter() -> None:
    assert period_from_token(None, now=NOW) is None
    assert period_from_token("all_time", now=NOW) is None
    assert period_from_token("", now=NOW) is None


def test_last_month() -> None:
    period = period_from_token("last_month", now=NOW)
    assert period is not None
    assert period.start == datetime(2026, 6, 1, tzinfo=UTC)
    assert period.end == datetime(2026, 7, 1, tzinfo=UTC)


def test_this_month() -> None:
    period = period_from_token("this_month", now=NOW)
    assert period is not None
    assert period.start == datetime(2026, 7, 1, tzinfo=UTC)
    assert period.end == datetime(2026, 8, 1, tzinfo=UTC)


def test_this_year_and_last_year() -> None:
    this_year = period_from_token("this_year", now=NOW)
    last_year = period_from_token("last_year", now=NOW)
    assert this_year is not None and last_year is not None
    assert this_year.start == datetime(2026, 1, 1, tzinfo=UTC)
    assert this_year.end == datetime(2027, 1, 1, tzinfo=UTC)
    assert last_year.start == datetime(2025, 1, 1, tzinfo=UTC)
    assert last_year.end == datetime(2026, 1, 1, tzinfo=UTC)


def test_last_quarter() -> None:
    # Jul 15 -> current quarter starts Jul 1, previous quarter is Apr–Jun.
    period = period_from_token("last_quarter", now=NOW)
    assert period is not None
    assert period.start == datetime(2026, 4, 1, tzinfo=UTC)
    assert period.end == datetime(2026, 7, 1, tzinfo=UTC)


def test_last_7_days_is_half_open_and_includes_today() -> None:
    period = period_from_token("last_7_days", now=NOW)
    assert period is not None
    assert period.start == datetime(2026, 7, 9, tzinfo=UTC)
    assert period.end == datetime(2026, 7, 16, tzinfo=UTC)
    assert (period.end - period.start).days == 7


def test_unknown_token_returns_none() -> None:
    assert period_from_token("since_the_dawn_of_time", now=NOW) is None


@pytest.mark.parametrize("token", RELATIVE_PERIODS)
def test_all_declared_tokens_resolve_or_are_all_time(token: str) -> None:
    result = period_from_token(token, now=NOW)
    if token == "all_time":
        assert result is None
    else:
        assert result is not None
        assert result.start < result.end
