"""The Typer application: wires up implemented commands and registers the roadmap stubs."""

from __future__ import annotations

import typer
from rich.console import Console

from insyte import __version__
from insyte.cli import (
    analyze_command,
    chat_command,
    connect_command,
    doctor_command,
    history_command,
    init_command,
    profile_command,
    query_command,
    scan_command,
    schema_command,
    status_command,
    studio_command,
    sync_command,
)
from insyte.cli._stubs import register_stub_commands
from insyte.cli.mcp_command import mcp_app
from insyte.cli.metrics_command import metrics_app
from insyte.cli.semantic_command import semantic_app

console = Console()

app = typer.Typer(
    name="insyte",
    help="Insyte — local-first AI analytics over your database, safely.",
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"insyte {__version__}")
        raise typer.Exit()


@app.callback()
def main_callback(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show the Insyte version and exit.",
    ),
) -> None:
    """Insyte analyses your database safely with AI, using read-only credentials."""


# Implemented commands.
app.command("init", help="Create a new Insyte project")(init_command.init)
app.command("connect", help="Test the read-only database connection")(connect_command.connect)
app.command("scan", help="Scan database schema into local metadata")(scan_command.scan)
app.command("schema", help="Display the scanned schema")(schema_command.schema)
app.command("query", help="Validate and run a read-only SQL query")(query_command.query)
app.command("profile", help="Profile columns with safe sampling")(profile_command.profile)
app.command("sync", help="Sync approved tables into the local DuckDB copy")(sync_command.sync)
app.command("analyze", help="Analyse a metric (time series, segment, or compare)")(
    analyze_command.analyze
)
app.command("chat", help="Open the interactive terminal analytics UI")(chat_command.chat)
app.command("studio", help="Open the browser-based analytics workspace")(studio_command.studio)
app.command("history", help="Show query history and security events")(history_command.history)
app.command("status", help="Show the active project's configuration")(status_command.status)
app.command("doctor", help="Run environment and configuration health checks")(doctor_command.doctor)

# Command groups.
app.add_typer(metrics_app, name="metrics")
app.add_typer(semantic_app, name="semantic")

# MCP server and client installers.
app.add_typer(mcp_app, name="mcp")

# Roadmap commands (registered as stubs so --help shows the full surface).
register_stub_commands(app)
