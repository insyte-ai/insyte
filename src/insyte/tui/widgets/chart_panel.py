"""Render an analysis result's chart to a string using Plotext."""

from __future__ import annotations

from insyte.analytics.models import AnalysisResult, ChartType


def render_chart_text(result: AnalysisResult, *, width: int = 70, height: int = 18) -> str | None:
    """Return a Plotext-rendered chart string, or None when no chart applies."""

    if result.chart.type is ChartType.none or len(result.rows) < 2:
        return None
    labels = [str(row[0]) for row in result.rows]
    values = [_as_float(row[1]) for row in result.rows]
    if any(value is None for value in values):
        return None
    try:
        import plotext as plt

        plt.clear_figure()
        plt.plotsize(width, height)
        plt.theme("clear")
        if result.chart.type is ChartType.line:
            plt.plot(values)
            plt.xticks(range(len(labels)), labels)
        else:
            plt.bar(labels, values, orientation="horizontal")
        plt.title(result.chart.title)
        return plt.build()
    except Exception:  # noqa: BLE001 - charting is best-effort, never fatal
        return None


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
