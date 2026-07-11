"""``insyte profile`` — safely profile columns using controlled sampling."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from insyte.cli._project import resolve_config
from insyte.config import paths
from insyte.config.models import InsyteConfig
from insyte.config.secrets import resolve_database_url
from insyte.connectors.postgres import PostgresConnector
from insyte.exceptions import (
    DatabaseConnectionError,
    SecretResolutionError,
    UnsupportedDatabaseError,
)
from insyte.logging_config import configure_logging
from insyte.metadata.models import ColumnProfile, ProfileResult
from insyte.metadata.profiler import Profiler
from insyte.metadata.repository import MetadataRepository

console = Console()


def _make_profiler(
    database_url: str, config: InsyteConfig, metadata: MetadataRepository
) -> Profiler:
    """Build a profiler. Overridden in tests to avoid a live database."""

    connector = PostgresConnector(database_url, config.database, config.query)
    return Profiler(connector, metadata, config.profiling)


def profile(
    project: str | None = typer.Option(None, "--project", "-p", help="Project to profile."),
) -> None:
    """Profile scanned columns (null %, distinct, cardinality, PII) using safe sampling."""

    config = resolve_config(project)
    if not config.profiling.enabled:
        console.print("[yellow]Profiling is disabled[/yellow] (profiling.enabled is false).")
        raise typer.Exit(1)

    configure_logging(log_file=paths.logs_dir(config.project.name) / "insyte.log")

    metadata = MetadataRepository(paths.metadata_path(config.project.name))
    try:
        if not metadata.has_metadata():
            console.print(
                "[yellow]No schema metadata yet.[/yellow] Run [bold]insyte scan[/bold] first."
            )
            raise typer.Exit(1)

        try:
            database_url = resolve_database_url(config.database, config.project.name)
        except SecretResolutionError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from exc

        profiler = _make_profiler(database_url, config, metadata)
        try:
            with console.status("[bold]Profiling columns…[/bold]", spinner="dots"):
                result = profiler.profile()
        except UnsupportedDatabaseError as exc:
            console.print(f"[red]Error:[/red] {exc}")
            raise typer.Exit(1) from exc
        except DatabaseConnectionError as exc:
            console.print(f"[red]Error:[/red] {exc} Run [bold]insyte doctor[/bold].")
            raise typer.Exit(1) from exc

        metadata.save_profiles(result)
        _render(config, result)
    finally:
        metadata.dispose()


def _render(config: InsyteConfig, result: ProfileResult) -> None:
    pii_count = sum(1 for c in result.column_profiles if c.is_pii)
    console.print(
        f"Profiled [bold]{len(result.table_profiles)}[/bold] tables, "
        f"[bold]{len(result.column_profiles)}[/bold] columns "
        f"([yellow]{pii_count} possible PII[/yellow]).\n"
    )

    by_table: dict[str, list] = {}
    for column in result.column_profiles:
        by_table.setdefault(f"{column.schema}.{column.table}", []).append(column)

    for table_name, columns in by_table.items():
        table = Table(title=table_name, title_justify="left")
        table.add_column("Column")
        table.add_column("Null %", justify="right")
        table.add_column("Distinct", justify="right")
        table.add_column("Cardinality")
        table.add_column("PII")
        table.add_column("Top / range", overflow="fold")
        for column in columns:
            table.add_row(
                column.column,
                f"{column.null_fraction * 100:.0f}%",
                str(column.distinct_estimate),
                column.cardinality.value,
                _pii_label(column),
                _sample_hint(column),
            )
        console.print(table)

    console.print(
        "\n[green]Profiles saved.[/green] Next: [bold]insyte semantic generate[/bold] "
        "to suggest metrics."
    )


def _pii_label(column: ColumnProfile) -> str:
    if not column.is_pii:
        return ""
    return f"[red]{column.pii_type}[/red]"


def _sample_hint(column: ColumnProfile) -> str:
    if column.top_values:
        top = ", ".join(f"{value} ({count})" for value, count in column.top_values[:3])
        return top
    if column.min_value is not None or column.max_value is not None:
        return f"{column.min_value} … {column.max_value}"
    return ""
