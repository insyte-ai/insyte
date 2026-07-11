"""``insyte status`` — summarise the active project's configuration.

Shows configuration and on-disk state only; it never opens a database connection. The
database URL is never displayed — only whether its environment variable is currently set.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from insyte.config import loader, paths
from insyte.config.secrets import database_url_is_available
from insyte.exceptions import InsyteError
from insyte.metadata.repository import MetadataRepository

console = Console()


def _scan_status(project: str) -> str:
    """Return a human-readable scan status by reading local metadata, if present."""

    metadata_path = paths.metadata_path(project)
    if not metadata_path.exists():
        return "[dim]no[/dim]"
    repository = MetadataRepository(metadata_path)
    try:
        latest = repository.latest_scan()
    finally:
        repository.dispose()
    if latest is None:
        return "[dim]no[/dim]"
    when = latest.finished_at.astimezone().strftime("%Y-%m-%d %H:%M")
    return f"[green]yes[/green] [dim]({when}, {latest.table_count} tables)[/dim]"


def status() -> None:
    """Show the active project's configuration and local state."""

    projects = loader.list_projects()
    if not projects:
        console.print(
            "[yellow]No Insyte projects found.[/yellow] Run [bold]insyte init[/bold] to create one."
        )
        raise typer.Exit(0)

    active = paths.get_active_project()
    if active not in projects:
        active = projects[0]

    try:
        config = loader.load_config(active)
    except InsyteError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    pdir = paths.project_dir(active)
    url_ready = database_url_is_available(config.database, active)
    scanned = _scan_status(active)

    table = Table(title=f"Insyte · {active}", show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("Project", f"[bold]{active}[/bold]")
    table.add_row("Database", config.database.type.value)
    table.add_row("URL env var", config.database.url_env)
    table.add_row(
        "URL available",
        "[green]yes[/green]" if url_ready else "[yellow]no (env var not set)[/yellow]",
    )
    table.add_row("SSL mode", config.database.ssl_mode.value)
    table.add_row("Allowed schemas", ", ".join(config.database.allowed_schemas) or "—")
    table.add_row("Blocked tables", str(len(config.database.blocked_tables)))
    table.add_row("Blocked columns", str(len(config.database.blocked_columns)))
    table.add_row("Analytics mode", config.analytics.mode.value)
    table.add_row(
        "AI integration",
        ", ".join(c.value for c in config.ai.integration) or "none",
    )
    table.add_row("Schema scanned", scanned)
    table.add_row("Project dir", str(pdir))

    console.print(table)

    if len(projects) > 1:
        others = ", ".join(p for p in projects if p != active)
        console.print(f"\n[dim]Other projects:[/dim] {others}")
