"""Deterministic grounding for the opt-in detailed report (feature: analyst reports).

The AI writes prose only; *this* module supplies every number it is allowed to reason about.
It assembles a JSON-serialisable payload from the already-computed (validated, PII-masked,
row-limited) analysis result plus Insyte's own deterministic metadata — data-quality flags from
profiling and forecast bands from real monthly actuals. Nothing here touches the database or the
network, so it is fully unit-testable and can never leak raw rows or credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime

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
_THIN_ROWS = 3
_FRESHNESS_WARNING_DAYS = 7
_FRESHNESS_STALE_DAYS = 30


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
        "contribution_summary": contribution_summary(domain),
        "outliers": outlier_flags(domain, fmt),
        "data_thinness": data_thinness_warnings(domain),
        "data_quality": data_quality_flags(profiles, tables),
        "freshness": {
            "mode": freshness_mode,
            "last_scan": last_scan.isoformat() if last_scan else None,
            "warnings": freshness_warnings(last_scan, now or datetime.now(), freshness_mode),
        },
    }
    if trend is not None and trend.rows:
        payload["trend_deltas"] = trend_deltas(trend, fmt)
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


def contribution_summary(domain: DomainAnalysisResult) -> dict:
    """Top-3/top-5 contribution concentration over already-aggregated contributors."""

    contributors = sorted(domain.contributors, key=lambda c: c.share, reverse=True)
    return {
        "top_3_share_pct": round(sum(c.share for c in contributors[:3]) * 100, 2),
        "top_5_share_pct": round(sum(c.share for c in contributors[:5]) * 100, 2),
        "contributor_count": len(contributors),
    }


def trend_deltas(domain: DomainAnalysisResult, fmt: MetricFormat) -> dict | None:
    """Latest-vs-prior deltas for a trend result."""

    numeric: list[tuple[object, float]] = []
    for row in domain.rows:
        if len(row) < 2:
            continue
        value = _float_or_none(row[1])
        if value is not None:
            numeric.append((row[0], value))
    if len(numeric) < 2:
        return None
    previous_label, previous = numeric[-2]
    latest_label, latest = numeric[-1]
    absolute = latest - previous
    percent = (absolute / previous * 100) if previous else None
    return {
        "latest_period": _scalar(latest_label),
        "previous_period": _scalar(previous_label),
        "latest_value": round(latest, 4),
        "latest_value_formatted": format_value(latest, fmt),
        "previous_value": round(previous, 4),
        "previous_value_formatted": format_value(previous, fmt),
        "absolute_change": round(absolute, 4),
        "absolute_change_formatted": format_value(absolute, fmt),
        "percent_change": round(percent, 2) if percent is not None else None,
    }


def outlier_flags(domain: DomainAnalysisResult, fmt: MetricFormat) -> list[dict]:
    """Simple z-score outlier flags over aggregate result values."""

    values: list[tuple[object, float]] = []
    for row in domain.rows:
        if len(row) < 2:
            continue
        value = _float_or_none(row[1])
        if value is not None:
            values.append((row[0], value))
    if len(values) < 4:
        return []
    nums = [v for _, v in values]
    mean = sum(nums) / len(nums)
    variance = sum((v - mean) ** 2 for v in nums) / len(nums)
    stddev = variance**0.5
    if stddev == 0:
        return []
    flags = []
    for label, value in values:
        z = (value - mean) / stddev
        if abs(z) >= 1.8:
            flags.append(
                {
                    "segment": str(_scalar(label)),
                    "value": round(value, 4),
                    "value_formatted": format_value(value, fmt),
                    "direction": "high" if z > 0 else "low",
                    "z_score": round(z, 2),
                }
            )
    return flags[:10]


def data_thinness_warnings(domain: DomainAnalysisResult) -> list[str]:
    warnings: list[str] = []
    if domain.row_count == 0:
        warnings.append("No result rows were returned for this analysis.")
    elif domain.row_count < _THIN_ROWS:
        warnings.append(
            f"Only {domain.row_count} aggregated row(s) support this result; "
            "confidence should be lower."
        )
    if domain.contributors and len(domain.contributors) < _THIN_ROWS:
        warnings.append("Contributor breakdown has fewer than three segments.")
    return warnings


def freshness_warnings(last_scan: datetime | None, now: datetime, mode: str) -> list[str]:
    if last_scan is None:
        return ["No schema scan timestamp is available."]
    last_scan = _normalise_datetime(last_scan)
    now = _normalise_datetime(now)
    age_days = (now - last_scan).days
    if mode != "direct" and age_days >= _FRESHNESS_STALE_DAYS:
        return [f"Local analytics data may be stale; last scan was {age_days} days ago."]
    if age_days >= _FRESHNESS_WARNING_DAYS:
        return [f"Metadata was last scanned {age_days} days ago."]
    return []


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

    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _normalise_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
