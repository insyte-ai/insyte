"""``insyte mcp`` — run the MCP server and install it into AI clients."""

from __future__ import annotations

import json
import os

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from insyte.analytics.engine import AnalyticsEngine
from insyte.cli._project import resolve_config
from insyte.config import paths
from insyte.config.secrets import (
    database_url_is_available,
    resolve_database_url,
    stored_database_url,
)
from insyte.connectors.factory import build_analytics_connector
from insyte.exceptions import InsyteError
from insyte.logging_config import configure_logging
from insyte.mcp.installer import (
    build_server_entry,
    client_target,
    install,
    is_installed,
    uninstall,
)
from insyte.mcp.server import build_mcp_server
from insyte.mcp.tools import AnalyticsBundle, InsyteToolService
from insyte.metadata.repository import MetadataRepository
from insyte.query.executor import QueryExecutor
from insyte.semantic.repository import SemanticRepository

console = Console()

mcp_app = typer.Typer(
    help="Run the Insyte MCP server and install it into Claude Code or Codex.",
    no_args_is_help=True,
    add_completion=False,
)


@mcp_app.command("start")
def start(
    project: str | None = typer.Option(None, "--project", "-p", help="Project to serve."),
) -> None:
    """Start the Insyte MCP server (stdio). Intended to be launched by an MCP client."""

    config = resolve_config(project)
    # stdout is the MCP protocol channel — logs must go to the file only.
    configure_logging(log_file=paths.logs_dir(config.project.name) / "mcp.log", force=True)

    layer = SemanticRepository(paths.semantic_path(config.project.name)).load()
    metadata = MetadataRepository(paths.metadata_path(config.project.name))

    def bundle_provider() -> AnalyticsBundle:
        connector = build_analytics_connector(config)
        executor = QueryExecutor(connector, config, metadata)
        relationships = metadata.list_relationships() if metadata.has_metadata() else []
        engine = AnalyticsEngine(executor, layer, relationships)
        return AnalyticsBundle(executor, engine)

    service = InsyteToolService(config, layer, metadata, bundle_provider)
    server = build_mcp_server(service)
    server.run()


@mcp_app.command("install")
def install_client(
    client: str = typer.Argument(..., help="Target client: 'claude' or 'codex'."),
    project: str | None = typer.Option(None, "--project", "-p", help="Project to serve."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not prompt for confirmation."),
    embed_secret: bool = typer.Option(
        False,
        "--embed-secret",
        help="Store the database URL in the client config (local file, never sent to the model).",
    ),
) -> None:
    """Install Insyte into an MCP client's configuration."""

    config = resolve_config(project)
    try:
        target = client_target(client)
    except InsyteError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    database_url = None
    if embed_secret:
        if database_url_is_available(config.database, config.project.name):
            database_url = resolve_database_url(config.database, config.project.name)
        else:
            console.print(
                f"[yellow]Note:[/yellow] {config.database.url_env} is not set; "
                "the URL will not be embedded."
            )

    entry = build_server_entry(
        config.project.name,
        insyte_home=os.environ.get("INSYTE_HOME"),
        database_url=database_url,
    )

    console.print(f"[dim]Client:[/dim] {target.name}   [dim]Config:[/dim] {target.config_path}")
    console.print(
        Panel(json.dumps({target.servers_key: {"insyte": entry}}, indent=2), title="Proposed entry")
    )
    if not database_url:
        if stored_database_url(config.project.name):
            console.print(
                "[dim]The server will use the URL you stored during 'insyte init' — no "
                "environment or --embed-secret needed.[/dim]"
            )
        else:
            console.print(
                "[dim]The server reads the database URL from its environment "
                f"({config.database.url_env}). Store it with 'insyte init --db-url …', "
                "or use --embed-secret here.[/dim]"
            )

    if not yes and not Confirm.ask("Write this entry?", default=True):
        console.print("[yellow]Aborted.[/yellow]")
        raise typer.Exit(0)

    install(target, entry)
    console.print(
        f"[green]Installed.[/green] Remove with [bold]insyte mcp uninstall {target.name}[/bold]."
    )


@mcp_app.command("uninstall")
def uninstall_client(
    client: str = typer.Argument(..., help="Target client: 'claude' or 'codex'."),
) -> None:
    """Remove Insyte from an MCP client's configuration."""

    try:
        target = client_target(client)
    except InsyteError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    if not is_installed(target):
        console.print(f"[dim]Insyte is not installed in {target.name}.[/dim]")
        raise typer.Exit(0)
    uninstall(target)
    console.print(f"[green]Removed Insyte from {target.name}.[/green]")
