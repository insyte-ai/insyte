"""A result card with Overview / Chart / Data / SQL tabs for an analysis result."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static, TabbedContent, TabPane

from insyte.analytics.models import AnalysisResult
from insyte.tui.widgets.chart_panel import render_chart_text
from insyte.tui.widgets.sql_panel import SQLPanel
from insyte.tui.widgets.table_panel import TablePanel


class ResultCard(Vertical):
    """Displays one analysis result with tabbed detail (spec §18/§19)."""

    def __init__(self, result: AnalysisResult) -> None:
        super().__init__(classes="result-card")
        self.result = result

    def compose(self) -> ComposeResult:
        result = self.result
        yield Static(f"[b]{result.label}[/b]", classes="card-title")
        yield Static(result.summary, classes="card-summary")
        with TabbedContent(initial="tab-overview"):
            with TabPane("Overview", id="tab-overview"):
                yield Static(self._overview(), classes="overview")
            with TabPane("Chart", id="tab-chart"):
                yield self._chart_widget()
            with TabPane("Data", id="tab-data"):
                yield TablePanel(result.columns, result.formatted_rows)
            with TabPane("SQL", id="tab-sql"):
                yield SQLPanel(
                    result.sql, result.row_count, result.duration_ms, _applied_limit(result)
                )

    def _overview(self) -> str:
        result = self.result
        lines = [
            result.summary,
            "",
            f"[dim]{result.row_count} rows · {result.duration_ms:.0f} ms · "
            f"chart: {result.chart.type.value}[/dim]",
        ]
        if result.contributors:
            lines.append("")
            lines.append("[b]Top contributors[/b]")
            for contributor in result.contributors[:5]:
                lines.append(f"  • {contributor.segment}: {contributor.share:.0%}")
        return "\n".join(lines)

    def _chart_widget(self) -> Static:
        chart = render_chart_text(self.result)
        if chart is None:
            return Static("No chart for this result.", classes="chart")
        return Static(Text.from_ansi(chart), classes="chart")


def _applied_limit(result: AnalysisResult) -> int | None:
    # The applied limit is not carried on AnalysisResult; the SQL already embeds it. Show rows.
    return None
