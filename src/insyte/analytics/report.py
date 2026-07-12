"""Deterministic grounding for the opt-in detailed report (feature: analyst reports).

The AI writes prose only; *this* module supplies every number it is allowed to reason about.
It assembles a JSON-serialisable payload from the already-computed (validated, PII-masked,
row-limited) analysis result plus Insyte's own deterministic metadata — data-quality flags from
profiling and forecast bands from real monthly actuals. Nothing here touches the database or the
network, so it is fully unit-testable and can never leak raw rows or credentials.
"""

from __future__ import annotations

from datetime import datetime

from insyte.analytics.charts import format_value
from insyte.analytics.forecast import project_current_year
from insyte.analytics.models import AnalysisResult as DomainAnalysisResult
from insyte.metadata.models import CardinalityCategory, ColumnProfile
from insyte.semantic.models import Metric, MetricFormat

# The model sees at most this many aggregated result rows. The result is already row-limited by
# the query pipeline; this is a second belt-and-braces cap on what leaves the machine.
MAX_REPORT_ROWS = 200

_NULL_WARNING = 0.2
_NULL_CRITICAL = 0.5
_DUPLICATE_NOTABLE = 0.5
_RUN_RATE_MONTHS = 3


def build_report_context(
    *,
    question: str,
    domain: DomainAnalysisResult,
    metric: Metric | None,
    fmt: MetricFormat,
    profiles: list[ColumnProfile],
    period_label: str | None,
    freshness_mode: str,
    last_scan: datetime | None,
    forecast_points: list[tuple[datetime, float]] | None = None,
    trend: DomainAnalysisResult | None = None,
    now: datetime | None = None,
) -> dict:
    """Assemble the grounded payload sent to the AI for a detailed report."""

    rows = domain.rows[:MAX_REPORT_ROWS]
    tables = _involved_tables(metric)
    payload: dict = {
        "question": question,
        "metric": {
            "name": domain.metric,
            "label": domain.label,
            "format": fmt.value,
            "currency_convention": "Indian (₹, lakh/crore)"
            if fmt is MetricFormat.currency
            else None,
        },
        "result_kind": domain.kind.value,
        "period": period_label,
        "columns": list(domain.columns),
        "rows": [[_scalar(v) for v in row] for row in rows],
        "row_count": domain.row_count,
        "truncated": domain.row_count > len(rows),
        "top_contributors": [
            {
                "segment": c.segment,
                "value": round(c.value, 4),
                "value_formatted": format_value(c.value, fmt),
                "share_pct": round(c.share * 100, 2),
            }
            for c in domain.contributors[:10]
        ],
        "data_quality": data_quality_flags(profiles, tables),
        "freshness": {
            "mode": freshness_mode,
            "last_scan": last_scan.isoformat() if last_scan else None,
        },
    }
    if trend is not None and trend.rows:
        payload["trend_series"] = {
            "grain": "month",
            "columns": list(trend.columns),
            "rows": [[_scalar(v) for v in row] for row in trend.rows[:MAX_REPORT_ROWS]],
            "note": (
                "Full monthly history for this metric — use it to assess direction, "
                "growth, month-over-month change, and seasonality."
            ),
        }
    if forecast_points:
        payload["forecast"] = forecast_bands(forecast_points, now or datetime.now(), fmt)
    return payload


def data_quality_flags(profiles: list[ColumnProfile], tables: set[str]) -> list[dict]:
    """Notable, deterministic data-quality issues for the tables behind this analysis."""

    flags: list[dict] = []
    for p in profiles:
        if tables and f"{p.schema}.{p.table}" not in tables:
            continue
        col = f"{p.table}.{p.column}"
        if p.null_fraction >= _NULL_CRITICAL:
            flags.append(
                _flag(
                    f"{p.null_fraction:.0%} of sampled values are missing",
                    "critical",
                    col,
                    "Aggregates over this column may understate the true total.",
                )
            )
        elif p.null_fraction >= _NULL_WARNING:
            flags.append(
                _flag(
                    f"{p.null_fraction:.0%} of sampled values are missing",
                    "warning",
                    col,
                    "Some rows are excluded from calculations on this column.",
                )
            )
        if p.cardinality is CardinalityCategory.empty:
            flags.append(
                _flag(
                    "Column is entirely empty in the sample",
                    "warning",
                    col,
                    "No signal available from this field.",
                )
            )
        elif p.cardinality is CardinalityCategory.constant:
            flags.append(
                _flag(
                    "Column has a single constant value",
                    "info",
                    col,
                    "Cannot segment or differentiate by this field.",
                )
            )
        if p.duplicate_ratio >= _DUPLICATE_NOTABLE and p.cardinality not in (
            CardinalityCategory.unique,
            CardinalityCategory.constant,
        ):
            flags.append(
                _flag(
                    f"{p.duplicate_ratio:.0%} duplicate values in the sample",
                    "info",
                    col,
                    "High repetition — verify grain before trusting counts.",
                )
            )
        if p.is_pii:
            kind = f" ({p.pii_type})" if p.pii_type else ""
            flags.append(
                _flag(
                    f"Detected as PII{kind}; values are masked",
                    "info",
                    col,
                    "Sensitive field — masked before any analysis or display.",
                )
            )
    order = {"critical": 0, "warning": 1, "info": 2}
    flags.sort(key=lambda f: order.get(f["severity"], 3))
    return flags


def forecast_bands(
    points: list[tuple[datetime, float]], now: datetime, fmt: MetricFormat
) -> dict | None:
    """Best / expected / worst full-year bands from real monthly actuals (deterministic)."""

    projection = project_current_year(points, now)
    if projection is None or projection.complete_months == 0:
        return None

    monthly = sorted((d, v) for d, v in points if v is not None)
    completed = [(d, v) for d, v in monthly if (d.year, d.month) < (now.year, now.month)]
    basis = [v for _, v in completed[-_RUN_RATE_MONTHS:]]
    if not basis:
        return None

    best_rate, worst_rate = max(basis), min(basis)
    remaining = projection.remaining_months
    ytd = projection.ytd_actual
    return {
        "expected": format_value(projection.projected_total, fmt),
        "best_case": format_value(ytd + best_rate * remaining, fmt),
        "worst_case": format_value(ytd + worst_rate * remaining, fmt),
        "assumptions": (
            f"{projection.complete_months} completed months are actuals; the remaining "
            f"{remaining} are projected at the last {len(basis)} months' run-rate. Best/worst "
            "use the highest/lowest of those months."
        ),
        "method": "trailing run-rate on real monthly actuals (not a model)",
    }


def _involved_tables(metric: Metric | None) -> set[str]:
    if metric is None or not metric.source_table:
        return set()
    return {metric.source_table}


def _flag(issue: str, severity: str, affected: str, impact: str) -> dict:
    return {"issue": issue, "severity": severity, "affected": affected, "impact": impact}


def _scalar(value: object) -> object:
    """Coerce a cell to a JSON-safe scalar (datetimes → iso, Decimal/other → str/float)."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)
