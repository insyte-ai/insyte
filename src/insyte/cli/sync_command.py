"""``insyte sync`` — copy approved tables into the local DuckDB analytical database."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from insyte.cli._project import resolve_config
from insyte.config import paths
from insyte.config.models import InsyteConfig
from insyte.config.secrets import resolve_database_url
from insyte.connectors.base import DatabaseConnector
from insyte.connectors.factory import duckdb_path
from insyte.connectors.postgres import PostgresConnector
from insyte.exceptions import (
    DatabaseConnectionError,
    SecretResolutionError,
    UnsupportedDatabaseError,
)
from insyte.logging_config import configure_logging
from insyte.metadata.repository import MetadataRepository
from insyte.warehouse.duckdb_manager import DuckDBManager
from insyte.warehouse.extractor import Extractor
from insyte.warehouse.model_builder import ensure_models
from insyte.warehouse.sync_engine import SyncEngine, SyncOutcome

console = Console()


def _make_source_connector(config: InsyteConfig) -> DatabaseConnector:
    """Build the source (PostgreSQL) connector to extract from. Overridden in tests."""

    database_url = resolve_database_url(config.database, config.project.name)
    return PostgresConnector(database_url, config.database, config.query)


def sync(
    table: list[str] | None = typer.Option(
        None,
        "--table",
        "-t",
        help="Table(s) to sync (repeatable). Defaults to already-synced tables.",
    ),
    incremental: bool = typer.Option(
        False, "--incremental", help="Only fetch rows after the last cursor, where possible."
    ),
    all_tables: bool = typer.Option(False, "--all", help="Sync all scanned tables."),
    status: bool = typer.Option(False, "--status", help="Show sync state and exit."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not prompt when syncing all tables."),
    project: str | None = typer.Option(None, "--project", "-p", help="Project to sync."),
) -> None:
    """Extract approved tables to Parquet and load them into the local DuckDB copy."""

    config = resolve_config(project)
    configure_logging(log_file=paths.logs_dir(config.project.name) / "insyte.log")
    metadata = MetadataRepository(paths.metadata_path(config.project.name))
    try:
        if status:
            _render_status(metadata)
            return
        if not metadata.has_metadata():
            console.print(
                "[yellow]No schema metadata yet.[/yellow] Run [bold]insyte scan[/bold] first."
            )
            raise typer.Exit(1)

        targets = _resolve_targets(metadata, table, all_tables, yes)
        _sync_targets(config, metadata, targets, incremental)
    finally:
        metadata.dispose()


def _resolve_targets(
    metadata: MetadataRepository, table: list[str] | None, all_tables: bool, yes: bool
) -> list[tuple[str, str]]:
    if table:
        targets: list[tuple[str, str]] = []
        for name in table:
            schema, _, bare = name.rpartition(".")
            detail = metadata.get_table(schema or None, bare)
            if detail is None:
                console.print(f"[red]Error:[/red] table {name!r} is not in the scanned metadata.")
                raise typer.Exit(1)
            targets.append((detail.summary.schema, detail.summary.name))
        return targets

    if all_tables:
        summaries = [s for s in metadata.list_tables() if s.kind == "table"]
        if not yes and not Confirm.ask(
            f"Sync all {len(summaries)} tables into DuckDB?", default=False
        ):
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(0)
        return [(s.schema, s.name) for s in summaries]

    states = metadata.list_sync_states()
    if not states:
        console.print(
            "[yellow]Nothing synced yet.[/yellow] Choose tables with "
            "[bold]--table orders[/bold] (repeatable) or [bold]--all[/bold]."
        )
        raise typer.Exit(0)
    return [tuple(s.table.split(".", 1)) for s in states]  # type: ignore[misc]


def _sync_targets(
    config: InsyteConfig,
    metadata: MetadataRepository,
    targets: list[tuple[str, str]],
    incremental: bool,
) -> None:
    try:
        connector = _make_source_connector(config)
    except SecretResolutionError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    manager = DuckDBManager(duckdb_path(config))
    extractor = Extractor(connector, paths.cache_dir(config.project.name))
    engine = SyncEngine(metadata, extractor, manager)

    outcomes: list[SyncOutcome] = []
    try:
        with console.status("[bold]Syncing…[/bold]", spinner="dots"):
            for schema, name in targets:
                outcomes.append(engine.sync_table(schema, name, incremental=incremental))
        ensure_models(manager, metadata.list_sync_states())
    except (UnsupportedDatabaseError, DatabaseConnectionError) as exc:
        console.print(f"[red]Error:[/red] {exc} Run [bold]insyte doctor[/bold].")
        raise typer.Exit(1) from exc
    finally:
        connector.dispose()

    _render_outcomes(config, outcomes)


def _render_outcomes(config: InsyteConfig, outcomes: list[SyncOutcome]) -> None:
    table = Table(title="Sync results", title_justify="left")
    table.add_column("Table")
    table.add_column("Mode")
    table.add_column("Rows", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Cursor")
    table.add_column("Status")
    for outcome in outcomes:
        style = "green" if outcome.status == "completed" else "red"
        cursor = outcome.cursor_column or "—"
        table.add_row(
            outcome.table,
            outcome.mode,
            str(outcome.rows),
            str(outcome.total_rows),
            cursor,
            f"[{style}]{outcome.status}[/{style}]"
            + (f" ({outcome.error})" if outcome.error else ""),
        )
    console.print(table)
    console.print(f"[dim]DuckDB:[/dim] {duckdb_path(config)}")
    if config.analytics.mode.value != "local":
        console.print(
            "[dim]Set [bold]analytics.mode: local[/bold] in config.yaml to run "
            "analyses against this local copy.[/dim]"
        )


def _render_status(metadata: MetadataRepository) -> None:
    states = metadata.list_sync_states()
    if not states:
        console.print("[dim]No tables have been synced yet.[/dim]")
        return
    table = Table(title="Sync state", title_justify="left")
    table.add_column("Table")
    table.add_column("Rows", justify="right")
    table.add_column("Cursor column")
    table.add_column("Last cursor")
    table.add_column("Mode")
    for state in states:
        table.add_row(
            state.table,
            str(state.row_count),
            state.cursor_column or "—",
            state.last_cursor or "—",
            state.mode,
        )
    console.print(table)
