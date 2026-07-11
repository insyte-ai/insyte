"""Chart recommendation and value formatting (spec §20).

Chart selection is a pure function of the analysis kind and result shape; a chart is only
recommended when it genuinely helps (never for a single aggregate value).
"""

from __future__ import annotations

from insyte.analytics.models import AnalysisKind, ChartSpec, ChartType
from insyte.semantic.models import MetricFormat

# Above this many segments, a horizontal bar reads better than a vertical one.
_HORIZONTAL_BAR_THRESHOLD = 6


def recommend_chart(
    kind: AnalysisKind, columns: list[str], row_count: int, label: str
) -> ChartSpec:
    """Recommend a chart type for a result."""

    if kind is AnalysisKind.timeseries and row_count >= 2:
        return ChartSpec(ChartType.line, title=label, x_label=_col(columns, 0), y_label=label)
    if kind is AnalysisKind.segment and row_count >= 1:
        chart = ChartType.horizontal_bar if row_count > _HORIZONTAL_BAR_THRESHOLD else ChartType.bar
        return ChartSpec(chart, title=label, x_label=_col(columns, 0), y_label=label)
    return ChartSpec(ChartType.none, title=label)


def _col(columns: list[str], index: int) -> str | None:
    return columns[index] if index < len(columns) else None


def format_value(value: object, fmt: MetricFormat) -> str:
    """Format a scalar metric value for display."""

    if value is None:
        return "—"
    number = _as_float(value)
    if number is None:
        return str(value)
    if fmt is MetricFormat.percent:
        return f"{number * 100:.1f}%"
    if fmt is MetricFormat.currency:
        return _compact_number(number, prefix="₹")
    return _compact_number(number)


def _compact_number(number: float, prefix: str = "") -> str:
    """Compact number in the Indian system: thousand (K), lakh (L), crore (Cr)."""

    magnitude = abs(number)
    if magnitude >= 10_000_000:  # 1 crore = 1,00,00,000
        return f"{prefix}{number / 10_000_000:.2f} Cr"
    if magnitude >= 100_000:  # 1 lakh = 1,00,000
        return f"{prefix}{number / 100_000:.2f} L"
    if magnitude >= 1_000:
        return f"{prefix}{number / 1_000:.1f} K"
    if number == int(number):
        return f"{prefix}{int(number)}"
    return f"{prefix}{number:.2f}"


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
