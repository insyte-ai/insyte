"""``insyte analyze`` — run a metric analysis (aggregate, time series, segment, or compare).

Examples:
    insyte analyze completed_revenue --grain week     # weekly time series
    insyte analyze completed_revenue --by city         # segment by a dimension
    insyte analyze payment_failure_rate                # single value
    insyte analyze completed_revenue --grain month --compare
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from insyte.analytics.engine import AnalyticsEngine
from insyte.analytics.models import AnalysisResult, ChartType, PeriodComparison, TimeGrain
from insyte.analytics.periods import periods_for_grain
from insyte.cli._project import resolve_config
from insyte.config import paths
from insyte.config.models import InsyteConfig
from insyte.connectors.factory import build_analytics_connector
from insyte.exceptions import (
    DatabaseConnectionError,
    InsyteError,
    QueryExecutionError,
    QueryValidationError,
    SecretResolutionError,
)
from insyte.logging_config import configure_logging
from insyte.metadata.repository import MetadataRepository
from insyte.query.executor import QueryExecutor
from insyte.semantic.repository import SemanticRepository

console = Console()


def _build_engine(config: InsyteConfig, recorder: MetadataRepository) -> AnalyticsEngine:
    """Build an analytics engine (direct or local warehouse). Overridden in tests."""

    connector = build_analytics_connector(config)
    executor = QueryExecutor(connector, config, recorder)
    layer = SemanticRepository(paths.semantic_path(config.project.name)).load()
    relationships = recorder.list_relationships() if recorder.has_metadata() else []
    return AnalyticsEngine(executor, layer, relationships)


def analyze(
    metric: str = typer.Argument(..., help="Metric name (see 'insyte metrics')."),
    by: str | None = typer.Option(None, "--by", help="Segment by this dimension."),
    grain: TimeGrain | None = typer.Option(
        None, "--grain", "-g", help="Time grain for a time series (day/week/month/quarter/year)."
    ),
    compare: bool = typer.Option(
        False, "--compare", help="Compare the current period with the previous one."
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Max segments to show."),
    project: str | None = typer.Option(None, "--project", "-p", help="Project to use."),
) -> None:
    """Analyse a metric and render a summary, table, and chart."""

    config = resolve_config(project)
    configure_logging(log_file=paths.logs_dir(config.project.name) / "insyte.log")

    recorder = MetadataRepository(paths.metadata_path(config.project.name))
    try:
        engine = _build_engine(config, recorder)
        if compare:
            grain = grain or TimeGrain.month
            current, baseline = periods_for_grain(grain)
            comparison = engine.compare(metric, current, baseline)
            _render_comparison(comparison)
        elif by is not None:
            _render_result(engine.segment(metric, by, limit=limit))
        elif grain is not None:
            _render_result(engine.timeseries(metric, grain))
        else:
            _render_result(engine.aggregate(metric))
    except (SecretResolutionError, QueryValidationError, QueryExecutionError, InsyteError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    except DatabaseConnectionError as exc:
        console.print(f"[red]Error:[/red] {exc} Run [bold]insyte doctor[/bold].")
        raise typer.Exit(1) from exc
    finally:
        recorder.dispose()


def _render_result(result: AnalysisResult) -> None:
    console.print(Panel(result.summary, border_style="green", title=result.label))

    table = Table(show_header=True, header_style="bold")
    for name in result.columns:
        table.add_column(name)
    for formatted in result.formatted_rows[:50]:
        table.add_row(*formatted)
    console.print(table)

    _render_chart(result)

    console.print(
        Panel(
            f"[cyan]{result.sql}[/cyan]\n\n"
            f"[dim]{result.row_count} rows · {result.duration_ms:.0f} ms · "
            f"chart: {result.chart.type.value}[/dim]",
            title="SQL",
            border_style="blue",
        )
    )


def _render_chart(result: AnalysisResult) -> None:
    if result.chart.type is ChartType.none or len(result.rows) < 2:
        return
    try:
        import plotext as plt

        labels = [str(row[0]) for row in result.rows]
        values = [_as_float(row[1]) for row in result.rows]
        if any(v is None for v in values):
            return
        plt.clear_figure()
        plt.plotsize(min(len(labels) * 6 + 20, 100), 18)
        if result.chart.type is ChartType.line:
            plt.plot(values)
            plt.xticks(range(len(labels)), labels)
        else:
            plt.bar(labels, values, orientation="horizontal")
        plt.title(result.chart.title)
        plt.theme("clear")
        plt.show()
    except Exception:  # noqa: BLE001 - charts are best-effort, never fatal
        return


def _render_comparison(comparison: PeriodComparison) -> None:
    console.print(Panel(comparison.summary, border_style="green", title=comparison.label))
    table = Table(show_header=True, header_style="bold")
    table.add_column("Period")
    table.add_column("Value", justify="right")
    table.add_row(comparison.current.label, _fmt(comparison.current_value))
    table.add_row(comparison.baseline.label, _fmt(comparison.baseline_value))
    if comparison.absolute_change is not None:
        pct = "" if comparison.percent_change is None else f" ({comparison.percent_change:+.1f}%)"
        table.add_row("Change", f"{comparison.absolute_change:+.2f}{pct}")
    console.print(table)
    console.print(
        Panel(
            f"[cyan]{comparison.sql_current}[/cyan]",
            title="SQL (current period)",
            border_style="blue",
        )
    )


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
