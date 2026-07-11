"""``insyte init`` — create a new project, interactively or from flags.

The wizard is interactive by default but accepts flags plus ``--yes`` for non-interactive
use (which the test suite and scripts rely on). It never accepts or stores a password: the
user supplies the *name* of the environment variable that holds the database URL.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from functools import partial
from typing import TypeVar

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from insyte.config import loader, paths
from insyte.config.models import (
    AIClient,
    AISection,
    AnalyticsMode,
    AnalyticsSection,
    DatabaseSection,
    DatabaseType,
    InsyteConfig,
    ProjectSection,
    SSLMode,
)
from insyte.config.secrets import database_url_is_available, store_database_url
from insyte.exceptions import InsyteError

console = Console()

_DEFAULT_URL_ENV = "INSYTE_DATABASE_URL"

_E = TypeVar("_E", bound=StrEnum)


def _select(prompt: str, choices: list[tuple[str, str]], default_index: int = 0) -> str:
    """Render a numbered menu and return the selected value."""

    console.print(f"\n[bold]{prompt}[/bold]")
    for i, (_, label) in enumerate(choices):
        marker = "[green]>[/green]" if i == default_index else " "
        console.print(f"  {marker} [cyan]{i + 1}[/cyan]. {label}")
    while True:
        raw = Prompt.ask("Choose", default=str(default_index + 1))
        try:
            idx = int(raw) - 1
        except ValueError:
            idx = -1
        if 0 <= idx < len(choices):
            return choices[idx][0]
        console.print("[red]Invalid choice, try again.[/red]")


def _parse_enum(value: str, enum: type[_E], field: str) -> _E:
    try:
        return enum(value)
    except ValueError as exc:
        allowed = ", ".join(e.value for e in enum)
        raise InsyteError(f"Invalid {field} {value!r}. Allowed values: {allowed}.") from exc


def init(
    name: str | None = typer.Option(None, "--name", help="Project name."),
    db_url: str | None = typer.Option(
        None, "--db-url", help="Database URL to store locally (a 0600 file, never config.yaml)."
    ),
    db_url_env: str | None = typer.Option(
        None, "--db-url-env", help="Environment variable that holds the database URL."
    ),
    schema: list[str] | None = typer.Option(
        None, "--schema", help="Allowed schema (repeatable). Defaults to 'public'."
    ),
    analytics_mode: str | None = typer.Option(
        None, "--analytics-mode", help="Analytics mode: 'direct' or 'local'."
    ),
    ai: list[str] | None = typer.Option(
        None, "--ai", help="AI client to integrate: 'claude' or 'codex' (repeatable)."
    ),
    ssl_mode: str | None = typer.Option(None, "--ssl-mode", help="PostgreSQL sslmode."),
    setup: bool = typer.Option(
        True,
        "--setup/--no-setup",
        help="After creating the project, connect, scan, generate metrics and wire up the AI tool.",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing project."),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Non-interactive: use flags and defaults, no prompts."
    ),
) -> None:
    """Create a new Insyte project and, interactively, run the full setup."""

    interactive = not yes

    try:
        # ---- Project name ------------------------------------------------------------
        if name is None:
            if not interactive:
                raise InsyteError(
                    "A project name is required. Pass --name in non-interactive mode."
                )
            name = Prompt.ask("Project name")
        paths.validate_project_name(name)

        # ---- Database URL: paste-and-store, or from an environment variable ----------
        resolved_url_env = db_url_env or _DEFAULT_URL_ENV
        stored_url: str | None = db_url
        if interactive and db_url is None and db_url_env is None:
            source = _select(
                "How should the database URL be provided?",
                [
                    ("paste", "Enter it now — stored locally (0600), not in config"),
                    ("env", "From an environment variable"),
                ],
            )
            if source == "paste":
                stored_url = Prompt.ask("Database URL", password=True).strip() or None
            else:
                resolved_url_env = Prompt.ask(
                    "Environment variable holding the database URL", default=_DEFAULT_URL_ENV
                )

        # ---- Sensible defaults (advanced users override with flags) ------------------
        allowed_schemas = list(schema) if schema else ["public"]
        mode = (
            _parse_enum(analytics_mode, AnalyticsMode, "analytics mode")
            if analytics_mode is not None
            else AnalyticsMode.direct
        )
        # 'prefer' negotiates SSL but falls back for local databases — the least-friction default.
        resolved_ssl = (
            _parse_enum(ssl_mode, SSLMode, "ssl mode") if ssl_mode is not None else SSLMode.prefer
        )

        # ---- AI tool -----------------------------------------------------------------
        if ai:
            ai_clients = [_parse_enum(c, AIClient, "ai client") for c in ai]
        elif interactive:
            selection = _select(
                "Which AI tool will you use for natural-language questions?",
                [
                    ("claude", "Claude Code"),
                    ("codex", "Codex"),
                    ("both", "Both"),
                    ("none", "None / decide later"),
                ],
            )
            ai_clients = {
                "claude": [AIClient.claude],
                "codex": [AIClient.codex],
                "both": [AIClient.claude, AIClient.codex],
                "none": [],
            }[selection]
        else:
            ai_clients = [AIClient.claude]

        config = InsyteConfig(
            project=ProjectSection(name=name),
            database=DatabaseSection(
                type=DatabaseType.postgresql,
                url_env=resolved_url_env,
                allowed_schemas=allowed_schemas,
                ssl_mode=resolved_ssl,
            ),
            analytics=AnalyticsSection(mode=mode),
            ai=AISection(integration=ai_clients),
        )

        # ---- Confirmation ------------------------------------------------------------
        if loader.project_exists(name) and not force:
            if interactive and Confirm.ask(
                f"Project {name!r} already exists. Overwrite?", default=False
            ):
                force = True
            else:
                raise InsyteError(f"Project {name!r} already exists. Use --force to overwrite.")

        if interactive and not Confirm.ask("Create project?", default=True):
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(0)

        loader.create_project(config, force=force)
        if stored_url:
            store_database_url(name, stored_url)

    except InsyteError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(
        Panel.fit(
            f"[bold green]Created project[/bold green] [bold]{name}[/bold]\n"
            f"[dim]{paths.project_dir(name)}[/dim]",
            title="Insyte",
            border_style="green",
        )
    )

    url_ready = database_url_is_available(config.database, name)
    if setup and interactive and url_ready:
        _run_guided_setup(ai_clients)
        _print_ready()
    elif url_ready:
        console.print(
            "\n[green]Database URL is set.[/green] Next: [bold]insyte connect[/bold], "
            "then [bold]insyte scan[/bold]."
        )
    else:
        console.print(
            f"\n[yellow]Note:[/yellow] no database URL yet. Set "
            f"[bold]{config.database.url_env}[/bold], or re-run with "
            '[bold]--db-url "postgresql://…"[/bold] to store it locally.'
        )


def _run_guided_setup(ai_clients: list[AIClient]) -> None:
    """Connect, scan, generate metrics, and wire up the chosen AI tool. Never crashes init."""

    from insyte.cli import connect_command, mcp_command, scan_command, semantic_command

    console.print("\n[bold]Setting up…[/bold]")

    def _step(label: str, fn: Callable[[], None]) -> bool:
        console.print(f"[cyan]›[/cyan] {label}")
        try:
            fn()
            return True
        except typer.Exit as exc:
            return not exc.exit_code
        except Exception as exc:  # noqa: BLE001 - setup must never abort project creation
            console.print(f"  [yellow]skipped:[/yellow] {exc}")
            return False

    if not _step(
        "Validating read-only connection",
        lambda: connect_command.connect(project=None, yes=True),
    ):
        console.print(
            "\n[yellow]Could not connect.[/yellow] Check the URL, then run "
            "[bold]insyte connect[/bold] and [bold]insyte scan[/bold] when it's fixed."
        )
        return
    if not _step("Scanning the schema", lambda: scan_command.scan(project=None)):
        return
    _step("Generating metrics", lambda: semantic_command.generate(project=None))
    for client in ai_clients:
        _step(
            f"Connecting {client.value.capitalize()}",
            partial(mcp_command.install_client, client.value, project=None, yes=True),
        )


def _print_ready() -> None:
    console.print(
        Panel.fit(
            "[bold green]Insyte is ready.[/bold green]\n\n"
            "[bold]insyte studio[/bold]   browser workspace (127.0.0.1:3838)\n"
            "[bold]insyte chat[/bold]     terminal UI\n"
            "[bold]insyte analyze[/bold]  a metric, e.g. [dim]... --by city[/dim]",
            title="Done",
            border_style="green",
        )
    )
