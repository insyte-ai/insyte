"""Registration of not-yet-implemented commands.

Every command from the product spec is registered so ``insyte --help`` shows the full
roadmap. Unimplemented commands print a clean notice pointing at the milestone that will
deliver them, and exit successfully.
"""

from __future__ import annotations

from collections.abc import Callable

import typer
from rich.console import Console

console = Console()

# command name -> (milestone, help text)
_STUBS: dict[str, tuple[int, str]] = {
    "serve": (7, "Run the Insyte HTTP API"),
}


def _coming_soon(command: str, milestone: int) -> None:
    console.print(
        f"[yellow]🚧 [bold]insyte {command}[/bold] is coming in Milestone {milestone}.[/yellow]"
    )
    console.print("[dim]This command is registered but not yet implemented.[/dim]")


def _make_stub(command: str, milestone: int) -> Callable[[], None]:
    def _stub() -> None:
        _coming_soon(command, milestone)

    _stub.__name__ = f"stub_{command.replace(' ', '_')}"
    return _stub


def register_stub_commands(app: typer.Typer) -> None:
    """Register all stubbed commands (and the ``mcp`` sub-app) on the given Typer app."""

    for name, (milestone, help_text) in _STUBS.items():
        app.command(name, help=f"{help_text} (Milestone {milestone})")(_make_stub(name, milestone))
