"""Deterministic year-end projection from real monthly actuals.

This is *not* a model guess — it is arithmetic on the metric's own history: completed months of
the current year plus a trailing run-rate for the months not yet elapsed. The result is always
labelled an estimate. No AI is involved; the LLM only recognises that the user asked for a
forecast and routes here, Insyte computes it from queried rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

_RUN_RATE_MONTHS = 3


@dataclass
class YearProjection:
    """A projected full-year total for a metric, with the inputs that produced it."""

    year: int
    ytd_actual: float  # sum of completed months in the current year
    projected_total: float  # ytd_actual + run_rate * remaining months
    run_rate: float  # average of the last N completed months (any year)
    complete_months: int  # completed months of the current year
    remaining_months: int  # months not yet complete (incl. the current partial month)
    basis_months: int  # how many months the run-rate averaged over


def project_current_year(
    points: list[tuple[datetime, float]], now: datetime
) -> YearProjection | None:
    """Project the current calendar year from monthly ``(period_start, value)`` actuals."""

    monthly = sorted((d, v) for d, v in points if v is not None)
    if not monthly:
        return None

    # "Completed" = months strictly before the current month (the current month is partial).
    completed = [(d, v) for d, v in monthly if (d.year, d.month) < (now.year, now.month)]
    ytd_actual = sum(v for d, v in completed if d.year == now.year)

    basis = completed[-_RUN_RATE_MONTHS:]
    run_rate = sum(v for _, v in basis) / len(basis) if basis else 0.0

    complete_months = now.month - 1
    remaining_months = 12 - complete_months  # includes the current, still-incomplete month
    projected_total = ytd_actual + run_rate * remaining_months

    return YearProjection(
        year=now.year,
        ytd_actual=ytd_actual,
        projected_total=projected_total,
        run_rate=run_rate,
        complete_months=complete_months,
        remaining_months=remaining_months,
        basis_months=len(basis),
    )
