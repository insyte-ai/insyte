"""Shared helpers for commands that operate on a project."""

from __future__ import annotations

import typer
from rich.console import Console

from insyte.config import loader, paths
from insyte.config.models import InsyteConfig
from insyte.exceptions import InsyteError

console = Console()


def resolve_config(project: str | None) -> InsyteConfig:
    """Load the requested project's config, or the active/only one.

    Prints a friendly message and raises ``typer.Exit(1)`` when no project can be resolved.
    """

    projects = loader.list_projects()
    if not projects:
        console.print(
            "[yellow]No Insyte projects found.[/yellow] Run [bold]insyte init[/bold] first."
        )
        raise typer.Exit(1)

    name = project or paths.get_active_project() or projects[0]
    if name not in projects:
        # On case-insensitive filesystems (macOS default) the on-disk folder and the stored
        # active-project name can differ only by case — match case-insensitively before failing.
        matches = [p for p in projects if p.lower() == name.lower()]
        if matches:
            name = matches[0]
        else:
            console.print(f"[red]Error:[/red] project {name!r} does not exist.")
            raise typer.Exit(1)

    try:
        return loader.load_config(name)
    except InsyteError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc
