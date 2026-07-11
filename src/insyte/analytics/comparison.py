"""Period comparison: compute deltas between a current and a baseline value."""

from __future__ import annotations

from insyte.analytics.charts import format_value
from insyte.analytics.models import Period, PeriodComparison
from insyte.semantic.models import Metric


def compute_comparison(
    metric_name: str,
    metric: Metric,
    current: Period,
    current_value: float | None,
    baseline: Period,
    baseline_value: float | None,
    sql_current: str,
    sql_baseline: str,
) -> PeriodComparison:
    """Assemble a :class:`PeriodComparison`, including absolute and percentage change."""

    absolute: float | None = None
    percent: float | None = None
    if current_value is not None and baseline_value is not None:
        absolute = current_value - baseline_value
        percent = (absolute / baseline_value * 100.0) if baseline_value else None

    return PeriodComparison(
        metric=metric_name,
        label=metric.label,
        current=current,
        baseline=baseline,
        current_value=current_value,
        baseline_value=baseline_value,
        absolute_change=absolute,
        percent_change=percent,
        sql_current=sql_current,
        sql_baseline=sql_baseline,
        summary=_summarise(metric, current, baseline, current_value, baseline_value, percent),
    )


def _summarise(
    metric: Metric,
    current: Period,
    baseline: Period,
    current_value: float | None,
    baseline_value: float | None,
    percent: float | None,
) -> str:
    if current_value is None or baseline_value is None:
        return f"{metric.label}: not enough data to compare."
    cur = format_value(current_value, metric.format)
    base = format_value(baseline_value, metric.format)
    if percent is None:
        direction = "changed"
        pct = ""
    else:
        direction = "increased" if percent >= 0 else "decreased"
        pct = f" by {abs(percent):.1f}%"
    return (
        f"{metric.label} {direction}{pct}, from {base} ({baseline.label}) "
        f"to {cur} ({current.label})."
    )
