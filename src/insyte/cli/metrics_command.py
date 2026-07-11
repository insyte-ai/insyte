"""``insyte metrics`` — list metrics/dimensions and approve suggested metrics."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from insyte.cli._project import resolve_config
from insyte.config import paths
from insyte.semantic.models import MetricStatus, SemanticLayer
from insyte.semantic.repository import SemanticRepository

console = Console()

metrics_app = typer.Typer(
    help="List semantic-layer metrics and dimensions, and approve suggestions.",
    invoke_without_command=True,
    no_args_is_help=False,
    add_completion=False,
)


@metrics_app.callback()
def metrics_main(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", "-p", help="Project to read."),
) -> None:
    """List metrics and dimensions when no subcommand is given."""

    if ctx.invoked_subcommand is not None:
        return
    config = resolve_config(project)
    layer = SemanticRepository(paths.semantic_path(config.project.name)).load()
    if not layer.metrics and not layer.dimensions:
        console.print(
            "[yellow]No metrics defined yet.[/yellow] Run "
            "[bold]insyte semantic generate[/bold] to suggest some from the schema."
        )
        raise typer.Exit(0)
    if layer.metrics:
        console.print(_metric_table(layer))
    if layer.dimensions:
        console.print(_dimension_table(layer))


@metrics_app.command("approve")
def approve(
    name: str = typer.Argument(..., help="Metric name to mark as confirmed."),
    project: str | None = typer.Option(None, "--project", "-p", help="Project to update."),
) -> None:
    """Approve a suggested metric (mark it confirmed)."""

    config = resolve_config(project)
    repository = SemanticRepository(paths.semantic_path(config.project.name))
    layer = repository.load()
    metric = layer.metrics.get(name)
    if metric is None:
        console.print(f"[red]Error:[/red] metric {name!r} is not defined.")
        raise typer.Exit(1)
    if metric.status is MetricStatus.confirmed:
        console.print(f"[dim]Metric {name!r} is already confirmed.[/dim]")
        raise typer.Exit(0)
    metric.status = MetricStatus.confirmed
    repository.save(layer)
    console.print(f"[green]Approved[/green] metric [bold]{name}[/bold] (now confirmed).")


def _metric_table(layer: SemanticLayer) -> Table:
    table = Table(title="Metrics", title_justify="left")
    table.add_column("Name", style="bold")
    table.add_column("Label")
    table.add_column("Status")
    table.add_column("Conf.", justify="right")
    table.add_column("Expression", overflow="fold")
    for name, metric in sorted(layer.metrics.items()):
        style = "green" if metric.status is MetricStatus.confirmed else "yellow"
        table.add_row(
            name,
            metric.label,
            f"[{style}]{metric.status.value}[/{style}]",
            f"{metric.confidence:.2f}",
            metric.expression,
        )
    return table


def _dimension_table(layer: SemanticLayer) -> Table:
    table = Table(title="Dimensions", title_justify="left")
    table.add_column("Name", style="bold")
    table.add_column("Source")
    table.add_column("Type")
    for name, dimension in sorted(layer.dimensions.items()):
        table.add_row(name, dimension.source, dimension.type)
    return table
