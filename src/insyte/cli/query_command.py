"""``insyte query "<sql>"`` — run a validated, read-only query directly.

This is the safe SQL pipeline exposed on the CLI: the SQL is parsed and validated, an
automatic row limit is applied, and only then is it executed read-only. Rejected queries show
the reason and never touch the database.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from insyte.cli._project import resolve_config
from insyte.config import paths
from insyte.config.models import InsyteConfig
from insyte.connectors.factory import build_analytics_connector
from insyte.exceptions import (
    DatabaseConnectionError,
    QueryExecutionError,
    QueryValidationError,
    SecretResolutionError,
    UnsupportedDatabaseError,
)
from insyte.logging_config import configure_logging
from insyte.metadata.repository import MetadataRepository
from insyte.query.executor import QueryExecutor
from insyte.query.models import ExecutionResult

console = Console()


def _make_executor(config: InsyteConfig, recorder: MetadataRepository) -> QueryExecutor:
    """Build an executor (direct or local warehouse). Overridden in tests."""

    connector = build_analytics_connector(config)
    return QueryExecutor(connector, config, recorder)


def query(
    sql: str = typer.Argument(..., help="The SQL query to validate and run."),
    project: str | None = typer.Option(
        None, "--project", "-p", help="Project to use (defaults to the active project)."
    ),
) -> None:
    """Validate and execute a read-only analytical query."""

    config = resolve_config(project)
    if not config.query.allow_direct_query:
        console.print(
            "[yellow]Direct queries are disabled[/yellow] "
            "(query.allow_direct_query is false) for this project."
        )
        raise typer.Exit(1)

    configure_logging(log_file=paths.logs_dir(config.project.name) / "insyte.log")

    recorder = MetadataRepository(paths.metadata_path(config.project.name))
    try:
        executor = _make_executor(config, recorder)
    except SecretResolutionError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        recorder.dispose()
        raise typer.Exit(1) from exc
    try:
        result = executor.execute(sql, source="direct")
    except QueryValidationError as exc:
        _render_blocked(exc.violations)
        raise typer.Exit(1) from exc
    except UnsupportedDatabaseError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    except (DatabaseConnectionError, QueryExecutionError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    finally:
        recorder.dispose()

    _render_result(config, result)


def _render_blocked(violations: list[str]) -> None:
    body = "[bold]Query blocked by Insyte.[/bold]\n\nReason:\n"
    body += "\n".join(f"- {v}" for v in violations)
    body += "\n\n[dim]No query was sent to the database.[/dim]"
    console.print(Panel(body, title="[red]Blocked[/red]", border_style="red"))


def _render_result(config: InsyteConfig, result: ExecutionResult) -> None:
    sql_panel = (
        f"[cyan]{result.normalized_sql}[/cyan]\n\n"
        "[green]✓[/green] Read-only   "
        "[green]✓[/green] Validated   "
        f"[green]✓[/green] Timeout: {config.query.timeout_seconds}s   "
        f"[green]✓[/green] Limit: {result.applied_limit}\n"
        f"[dim]Execution: {result.duration_ms:.0f} ms · {result.row_count} rows"
        f"{' (truncated)' if result.truncated else ''}[/dim]"
    )
    console.print(Panel(sql_panel, title="Validated SQL", border_style="green"))

    if not result.columns:
        return
    table = Table(show_header=True, header_style="bold")
    for name in result.columns:
        table.add_column(name)
    for row in result.rows[:100]:
        table.add_row(*[_format_cell(v) for v in row])
    console.print(table)
    if result.row_count > 100:
        console.print(f"[dim]… showing first 100 of {result.row_count} rows[/dim]")


def _format_cell(value: object) -> str:
    return "" if value is None else str(value)
