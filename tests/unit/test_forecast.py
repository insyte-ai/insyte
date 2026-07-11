"""Tests for deterministic year-end projection."""

from __future__ import annotations

from datetime import UTC, datetime

from insyte.analytics.forecast import project_current_year


def _monthly(year: int, values: list[float]) -> list[tuple[datetime, float]]:
    return [(datetime(year, i + 1, 1, tzinfo=UTC), v) for i, v in enumerate(values)]


def test_returns_none_without_data() -> None:
    assert project_current_year([], datetime(2026, 7, 15, tzinfo=UTC)) is None


def test_projects_from_trailing_run_rate() -> None:
    # Jan–Jun 2026 each 100 (Jun is the last completed month; Jul is current/partial).
    points = _monthly(2026, [100, 100, 100, 100, 100, 100])
    now = datetime(2026, 7, 15, tzinfo=UTC)
    proj = project_current_year(points, now)
    assert proj is not None
    assert proj.complete_months == 6  # Jan–Jun
    assert proj.ytd_actual == 600
    assert proj.run_rate == 100  # avg of last 3 completed (Apr,May,Jun)
    assert proj.remaining_months == 6  # Jul–Dec
    assert proj.projected_total == 600 + 100 * 6  # 1200


def test_run_rate_uses_last_three_completed_months() -> None:
    # Rising trend; run-rate should reflect the recent (higher) months, not the early ones.
    points = _monthly(2026, [10, 20, 30, 40, 50, 60])  # last 3 completed: 40,50,60 -> 50
    proj = project_current_year(points, datetime(2026, 7, 1, tzinfo=UTC))
    assert proj is not None
    assert proj.run_rate == 50
    assert proj.ytd_actual == 210
    assert proj.projected_total == 210 + 50 * 6


def test_january_projects_entirely_from_prior_year_run_rate() -> None:
    points = _monthly(2025, [100] * 12)
    now = datetime(2026, 1, 10, tzinfo=UTC)
    proj = project_current_year(points, now)
    assert proj is not None
    assert proj.complete_months == 0
    assert proj.ytd_actual == 0
    assert proj.run_rate == 100  # last 3 completed months of 2025
    assert proj.remaining_months == 12
    assert proj.projected_total == 1200
