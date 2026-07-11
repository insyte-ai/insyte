"""``insyte studio`` — launch the browser-based analytics workspace."""

from __future__ import annotations

import os
import socket
import threading
import time
import webbrowser
from urllib.error import URLError
from urllib.request import urlopen

import typer
import uvicorn
from rich.console import Console

from insyte.exceptions import InsyteError
from insyte.services.project_service import ProjectService, ProjectServices
from insyte.studio.app import STUDIO_PROJECT_ENV, create_studio_app

console = Console()

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3838


def find_available_port(preferred: int, host: str, attempts: int = 100) -> int:
    """Return the first bindable port at or after ``preferred``."""

    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise InsyteError(f"No available local port found near {preferred}.")


def _open_browser_when_ready(url: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    health = f"{url}/api/health"
    while time.monotonic() < deadline:
        try:
            with urlopen(health, timeout=0.5) as response:  # noqa: S310 - localhost only
                if response.status == 200:
                    break
        except (URLError, OSError):
            time.sleep(0.2)
    webbrowser.open_new_tab(url)


def studio(
    project: str | None = typer.Option(None, "--project", "-p", help="Project to open."),
    host: str = typer.Option(DEFAULT_HOST, "--host", help="Host to bind (localhost by default)."),
    port: int = typer.Option(DEFAULT_PORT, "--port", help="Preferred port."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open a browser."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev)."),
) -> None:
    """Start Insyte Studio and open it in your browser."""

    console.print("[bold]Starting Insyte Studio…[/bold]")

    try:
        services = ProjectService.open(project)
    except InsyteError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    _print_diagnostics(services)
    selected_port = find_available_port(port, host)
    url = f"http://{host}:{selected_port}"

    if not no_browser:
        threading.Thread(target=_open_browser_when_ready, args=(url,), daemon=True).start()

    console.print(f"[green]✓[/green] Studio running at [bold]{url}[/bold]")
    if not no_browser:
        console.print("[green]✓[/green] Opening browser")
    console.print("[dim]Press Ctrl+C to stop.[/dim]")

    if reload:
        # Reload re-imports the app, so it can't hold a live services object.
        services.dispose()
        os.environ[STUDIO_PROJECT_ENV] = services.config.project.name
        uvicorn.run(
            "insyte.studio.app:app_from_env",
            host=host,
            port=selected_port,
            reload=True,
            factory=True,
            log_level="warning",
        )
        return

    app = create_studio_app(services=services)
    try:
        uvicorn.run(app, host=host, port=selected_port, log_level="warning")
    finally:
        services.dispose()


def _print_diagnostics(services: ProjectServices) -> None:
    console.print(f"[green]✓[/green] Project loaded: [bold]{services.config.project.name}[/bold]")
    if services.schema.has_metadata():
        tables = len(services.schema.list_tables())
        console.print(f"[green]✓[/green] Schema metadata available ({tables} tables)")
    else:
        console.print("[yellow]![/yellow] No schema metadata yet — run [bold]insyte scan[/bold]")
    console.print(f"[green]✓[/green] Analytics mode: {services.config.analytics.mode.value}")
