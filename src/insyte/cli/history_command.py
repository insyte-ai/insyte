"""``insyte history`` — show recent query history and blocked-query security events."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from insyte.cli._project import resolve_config
from insyte.config import paths
from insyte.metadata.repository import MetadataRepository
from insyte.query.models import QueryHistoryEntry, SecurityEventEntry

console = Console()

_STATUS_STYLE = {"ok": "green", "blocked": "red", "error": "yellow"}


def history(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of recent entries to show."),
    project: str | None = typer.Option(
        None, "--project", "-p", help="Project to read (defaults to the active project)."
    ),
) -> None:
    """Show recent audited queries and security events."""

    config = resolve_config(project)
    metadata_path = paths.metadata_path(config.project.name)
    if not metadata_path.exists():
        console.print("[dim]No query history yet.[/dim]")
        raise typer.Exit(0)

    repository = MetadataRepository(metadata_path)
    try:
        queries = repository.list_query_history(limit)
        events = repository.list_security_events(limit)
    finally:
        repository.dispose()

    if not queries and not events:
        console.print("[dim]No query history yet.[/dim]")
        return

    if queries:
        console.print(_query_table(queries))
    if events:
        console.print(_event_table(events))


def _query_table(queries: list[QueryHistoryEntry]) -> Table:
    table = Table(title="Query history", title_justify="left")
    table.add_column("When", style="dim")
    table.add_column("Status")
    table.add_column("Src")
    table.add_column("Rows", justify="right")
    table.add_column("ms", justify="right")
    table.add_column("SQL", overflow="fold")
    for entry in queries:
        style = _STATUS_STYLE.get(entry.status, "white")
        table.add_row(
            _when(entry.created_at),
            f"[{style}]{entry.status}[/{style}]",
            entry.source,
            "" if entry.row_count is None else str(entry.row_count),
            "" if entry.duration_ms is None else f"{entry.duration_ms:.0f}",
            _truncate(entry.raw_sql),
        )
    return table


def _event_table(events: list[SecurityEventEntry]) -> Table:
    table = Table(title="Security events", title_justify="left", border_style="red")
    table.add_column("When", style="dim")
    table.add_column("Type")
    table.add_column("Violations", overflow="fold")
    for event in events:
        table.add_row(_when(event.created_at), event.event_type, "; ".join(event.violations))
    return table


def _when(value: object) -> str:
    if value is None:
        return ""
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")  # type: ignore[attr-defined]


def _truncate(sql: str, length: int = 80) -> str:
    collapsed = " ".join(sql.split())
    return collapsed if len(collapsed) <= length else collapsed[: length - 1] + "…"
