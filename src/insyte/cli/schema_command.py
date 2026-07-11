"""``insyte schema [TABLE]`` — display scanned schema and table detail.

With no argument it prints an overview (last scan, tables per schema, relationship map). With
a table name (``orders`` or ``public.orders``) it prints that table's columns, indexes and
relationships. Reads only local metadata — no database connection.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from insyte.cli._project import resolve_config
from insyte.config import paths
from insyte.metadata.models import (
    RelationshipInfo,
    ScanSummary,
    TableDetail,
    TableSummary,
)
from insyte.metadata.repository import MetadataRepository

console = Console()


def schema(
    table: str | None = typer.Argument(
        None, help="Optional table to describe, e.g. 'orders' or 'public.orders'."
    ),
    project: str | None = typer.Option(
        None, "--project", "-p", help="Project to read (defaults to the active project)."
    ),
) -> None:
    """Display the scanned schema, or one table's detail."""

    config = resolve_config(project)
    metadata_path = paths.metadata_path(config.project.name)
    if not metadata_path.exists():
        console.print("[yellow]No metadata yet.[/yellow] Run [bold]insyte scan[/bold] first.")
        raise typer.Exit(1)

    repository = MetadataRepository(metadata_path)
    try:
        if not repository.has_metadata():
            console.print("[yellow]No metadata yet.[/yellow] Run [bold]insyte scan[/bold] first.")
            raise typer.Exit(1)
        if table is None:
            _render_overview(repository)
        else:
            _render_table(repository, table)
    finally:
        repository.dispose()


def _render_overview(repository: MetadataRepository) -> None:
    latest = repository.latest_scan()
    if latest is not None:
        _render_scan_header(latest)

    tables = repository.list_tables()
    overview = Table(title="Tables", title_justify="left")
    overview.add_column("Table")
    overview.add_column("Kind")
    overview.add_column("Rows", justify="right")
    overview.add_column("Cols", justify="right")
    overview.add_column("Category")
    for summary in tables:
        overview.add_row(
            summary.qualified_name,
            summary.kind,
            _format_rows(summary.row_estimate),
            str(summary.column_count),
            _format_category(summary),
        )
    console.print(overview)

    relationships = repository.list_relationships()
    if relationships:
        console.print(_relationship_table(relationships, title="Relationship map"))


def _render_table(repository: MetadataRepository, table: str) -> None:
    schema_name, name = _split_table(table)
    detail = repository.get_table(schema_name, name)
    if detail is None:
        console.print(f"[red]Table {table!r} not found.[/red] Try [bold]insyte schema[/bold].")
        raise typer.Exit(1)

    _render_table_header(detail.summary)
    console.print(_column_table(detail))

    if detail.indexes:
        idx_table = Table(title="Indexes", title_justify="left")
        idx_table.add_column("Name")
        idx_table.add_column("Columns")
        idx_table.add_column("Unique")
        for idx in detail.indexes:
            idx_table.add_row(idx.name, ", ".join(idx.columns), "yes" if idx.is_unique else "no")
        console.print(idx_table)

    if detail.outgoing:
        console.print(_relationship_table(detail.outgoing, title="References (outgoing)"))
    if detail.incoming:
        console.print(_relationship_table(detail.incoming, title="Referenced by (incoming)"))


def _render_scan_header(latest: ScanSummary) -> None:
    when = latest.finished_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    console.print(
        f"[dim]Last scan:[/dim] {when}  "
        f"[dim]·[/dim] {latest.table_count} tables, {latest.view_count} views, "
        f"{latest.column_count} columns, {latest.relationship_count} relationships\n"
    )


def _render_table_header(summary: TableSummary) -> None:
    parts = [f"[bold]{summary.qualified_name}[/bold] [dim]({summary.kind})[/dim]"]
    parts.append(f"category: {_format_category(summary)}")
    if summary.row_estimate is not None:
        parts.append(f"~{_format_rows(summary.row_estimate)} rows")
    if summary.size_bytes is not None:
        parts.append(_format_bytes(summary.size_bytes))
    console.print("  ·  ".join(parts) + "\n")


def _column_table(detail: TableDetail) -> Table:
    table = Table(title="Columns", title_justify="left")
    table.add_column("Column")
    table.add_column("Type")
    table.add_column("Null")
    table.add_column("Key")
    table.add_column("Comment", overflow="fold")
    for col in detail.columns:
        key = "PK" if col.is_primary_key else ("UQ" if col.is_unique else "")
        table.add_row(
            col.name,
            col.data_type,
            "" if col.nullable else "not null",
            key,
            col.comment or "",
        )
    return table


def _relationship_table(relationships: list[RelationshipInfo], *, title: str) -> Table:
    table = Table(title=title, title_justify="left")
    table.add_column("From")
    table.add_column("To")
    table.add_column("Kind")
    table.add_column("Conf.", justify="right")
    for rel in relationships:
        source = f"{rel.source_qualified}.{','.join(rel.source_columns)}"
        target = f"{rel.target_qualified}.{','.join(rel.target_columns)}"
        kind = "FK" if rel.kind == "foreign_key" else "inferred"
        table.add_row(source, target, kind, f"{rel.confidence:.2f}")
    return table


def _split_table(value: str) -> tuple[str | None, str]:
    if "." in value:
        schema_name, name = value.split(".", 1)
        return schema_name, name
    return None, value


def _format_rows(rows: int | None) -> str:
    if rows is None or rows < 0:
        return "—"
    if rows >= 1_000_000:
        return f"{rows / 1_000_000:.1f}M"
    if rows >= 1_000:
        return f"{rows / 1_000:.1f}K"
    return str(rows)


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _format_category(summary: TableSummary) -> str:
    if summary.category == "unknown":
        return "[dim]unknown[/dim]"
    return f"{summary.category} [dim]({summary.category_confidence:.0%})[/dim]"
