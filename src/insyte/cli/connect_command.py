"""``insyte connect`` — validate the read-only database connection.

Resolves the database URL from the environment, opens a read-only transaction with timeouts,
runs ``SELECT 1``, verifies the server is PostgreSQL, checks SSL and permissions, and warns if
the account can write. The URL and credentials are never printed.
"""

from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from insyte.cli._project import resolve_config
from insyte.config import paths
from insyte.config.models import InsyteConfig
from insyte.config.secrets import resolve_database_url
from insyte.connectors.base import ConnectionCheckResult, DatabaseConnector
from insyte.connectors.postgres import PostgresConnector
from insyte.exceptions import (
    DatabaseConnectionError,
    SecretResolutionError,
    UnsupportedDatabaseError,
)
from insyte.logging_config import configure_logging

console = Console()


def _make_connector(database_url: str, config: InsyteConfig) -> DatabaseConnector:
    """Build a connector for the project. Overridden in tests via monkeypatch."""

    return PostgresConnector(database_url, config.database, config.query)


def _is_interactive() -> bool:
    return sys.stdin.isatty()


def connect(
    project: str | None = typer.Option(
        None, "--project", "-p", help="Project to use (defaults to the active project)."
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Do not prompt on the write-permission warning."
    ),
) -> None:
    """Test the read-only database connection for a project."""

    config = resolve_config(project)
    configure_logging(log_file=paths.logs_dir(config.project.name) / "insyte.log")

    try:
        database_url = resolve_database_url(config.database, config.project.name)
    except SecretResolutionError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    connector = _make_connector(database_url, config)
    try:
        with console.status("[bold]Connecting to the database…[/bold]", spinner="dots"):
            result = connector.check_connection()
    except UnsupportedDatabaseError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
    except DatabaseConnectionError as exc:
        _render_connection_error(exc)
        raise typer.Exit(1) from exc
    finally:
        connector.dispose()

    _render_success(config, result)
    _handle_write_warning(result, yes=yes)

    console.print("\n[green]Connection validated.[/green] Next: [bold]insyte scan[/bold].")


def _render_success(config: InsyteConfig, result: ConnectionCheckResult) -> None:
    server = result.server
    ssl = result.ssl

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Project", config.project.name)
    table.add_row("Database", server.database)
    table.add_row("User", server.user)
    table.add_row("Server", server.version.split(" on ")[0])
    table.add_row("PostgreSQL", "[green]yes[/green]" if server.is_postgres else "[red]no[/red]")
    if ssl.in_use:
        detail = ssl.protocol or "enabled"
        table.add_row("SSL", f"[green]{detail}[/green]")
    else:
        table.add_row("SSL", "[yellow]not in use[/yellow]")
    table.add_row("Read-only tx", "[green]enforced[/green]")
    table.add_row("Statement timeout", f"{result.statement_timeout_seconds}s")
    table.add_row("Lock timeout", f"{result.lock_timeout_seconds}s")
    table.add_row(
        "Write access",
        "[yellow]yes[/yellow]" if result.permissions.has_write_access else "[green]no[/green]",
    )

    console.print(
        Panel.fit(table, title="[bold green]Connected[/bold green]", border_style="green")
    )


def _handle_write_warning(result: ConnectionCheckResult, *, yes: bool) -> None:
    if not result.permissions.has_write_access:
        return

    lines = [
        "This database user has [bold]write permissions[/bold].",
        "Insyte will still enforce read-only transactions, but a dedicated read-only",
        "database account is strongly recommended.",
    ]
    if result.permissions.write_samples:
        sample = ", ".join(result.permissions.write_samples[:3])
        lines.append(f"\n[dim]Examples:[/dim] {sample}")
    console.print(Panel("\n".join(lines), title="[yellow]Warning[/yellow]", border_style="yellow"))

    if yes or not _is_interactive():
        return
    if not Confirm.ask("Continue?", default=False):
        console.print("[yellow]Stopped.[/yellow]")
        raise typer.Exit(0)


def _render_connection_error(exc: DatabaseConnectionError) -> None:
    host = exc.host or "unknown"
    port = exc.port or 5432
    body = f"[bold]Unable to connect to PostgreSQL.[/bold]\n\nHost: {host}\nPort: {port}\n"
    if exc.reason:
        body += f"\nDetail: {exc.reason}\n"
    body += (
        "\nPossible causes:\n"
        "- The host cannot be reached.\n"
        "- PostgreSQL is not accepting remote connections.\n"
        "- SSL is required but not configured.\n"
        "- The credentials are incorrect.\n\n"
        "Run: [bold]insyte doctor[/bold]"
    )
    console.print(Panel(body, title="[red]Connection failed[/red]", border_style="red"))
