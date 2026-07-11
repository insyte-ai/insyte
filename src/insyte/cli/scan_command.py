"""``insyte scan`` — read the database schema into local metadata.

Scans schemas, tables, views, columns, keys, indexes, estimated sizes and comments inside a
read-only transaction, detects relationships, classifies tables, and stores everything in the
project's ``metadata.sqlite``. Blocked tables and columns are never recorded.
"""

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
from insyte.metadata.models import RelationshipKind, ScanResult, ScanSummary, TableKind
from insyte.metadata.repository import MetadataRepository, utcnow
from insyte.metadata.scanner import SchemaScanner

console = Console()


def _make_scanner(database_url: str, config: InsyteConfig) -> SchemaScanner:
    """Build a scanner. Overridden in tests to avoid a live database."""

    connector = PostgresConnector(database_url, config.database, config.query)
    return SchemaScanner(connector, config.database)


def scan(
    project: str | None = typer.Option(
        None, "--project", "-p", help="Project to scan (defaults to the active project)."
    ),
) -> None:
    """Scan the database schema into local metadata."""

    config = resolve_config(project)
    configure_logging(log_file=paths.logs_dir(config.project.name) / "insyte.log")

    try:
        database_url = resolve_database_url(config.database, config.project.name)
    except SecretResolutionError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    scanner = _make_scanner(database_url, config)
    started = utcnow()
    try:
        with console.status("[bold]Scanning schema…[/bold]", spinner="dots"):
            result = scanner.scan()
    except UnsupportedDatabaseError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    except DatabaseConnectionError as exc:
        console.print(f"[red]Error:[/red] {exc} Run [bold]insyte doctor[/bold].")
        raise typer.Exit(1) from exc

    repository = MetadataRepository(paths.metadata_path(config.project.name))
    try:
        summary = repository.save_scan(result, started_at=started, finished_at=utcnow())
    finally:
        repository.dispose()

    _render_summary(config, result, summary)
    console.print("\n[green]Scan complete.[/green] Next: [bold]insyte schema[/bold].")


def _render_summary(config: InsyteConfig, result: ScanResult, summary: ScanSummary) -> None:
    fk_count = sum(1 for r in result.relationships if r.kind is RelationshipKind.foreign_key)
    inferred_count = summary.relationship_count - fk_count

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Project", config.project.name)
    table.add_row("Schemas", str(summary.schema_count))
    table.add_row("Tables", str(summary.table_count))
    table.add_row("Views", str(summary.view_count))
    table.add_row("Columns", str(summary.column_count))
    rel_detail = f"[dim]({fk_count} foreign key, {inferred_count} inferred)[/dim]"
    table.add_row("Relationships", f"{summary.relationship_count}  {rel_detail}")
    console.print(table)

    per_schema = Table(title="By schema", title_justify="left")
    per_schema.add_column("Schema")
    per_schema.add_column("Tables", justify="right")
    per_schema.add_column("Views", justify="right")
    for schema in sorted(result.schemas):
        tables = [t for t in result.tables if t.schema == schema]
        views = sum(1 for t in tables if t.kind is TableKind.view)
        per_schema.add_row(schema, str(len(tables) - views), str(views))
    console.print(per_schema)
