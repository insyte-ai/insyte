"""``insyte doctor`` — environment and configuration health checks.

Milestone 1 checks the Python runtime, required packages, the writability of the Insyte home
directory, and that the active project's config is valid. It does not connect to a database
(that check arrives with ``insyte connect`` in Milestone 2). Exits non-zero if any hard check
fails.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass

import typer
from rich.console import Console
from rich.table import Table

from insyte import __version__
from insyte.config import loader, paths
from insyte.config.secrets import database_url_is_available
from insyte.exceptions import InsyteError

console = Console()

_REQUIRED_PACKAGES = ("typer", "rich", "pydantic", "pydantic_settings", "yaml")
_MIN_PYTHON = (3, 11)

_OK = "ok"
_WARN = "warn"
_FAIL = "fail"

_ICON = {_OK: "[green]✓[/green]", _WARN: "[yellow]⚠[/yellow]", _FAIL: "[red]✗[/red]"}


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _check_python() -> Check:
    v = sys.version_info
    version = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= _MIN_PYTHON:
        return Check("Python runtime", _OK, version)
    return Check("Python runtime", _FAIL, f"{version} (need >= 3.11)")


def _check_packages() -> list[Check]:
    checks: list[Check] = []
    for pkg in _REQUIRED_PACKAGES:
        try:
            importlib.import_module(pkg)
            checks.append(Check(f"Package '{pkg}'", _OK, "importable"))
        except ImportError:
            checks.append(Check(f"Package '{pkg}'", _FAIL, "not installed"))
    return checks


def _check_home() -> Check:
    home = paths.insyte_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".insyte_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return Check("Insyte home writable", _OK, str(home))
    except OSError as exc:
        return Check("Insyte home writable", _FAIL, f"{home}: {exc}")


def _check_active_project() -> list[Check]:
    projects = loader.list_projects()
    if not projects:
        return [Check("Projects", _WARN, "none found — run 'insyte init'")]

    active = paths.get_active_project()
    if active not in projects:
        active = projects[0]

    checks = [Check("Projects", _OK, f"{len(projects)} found · active: {active}")]
    try:
        config = loader.load_config(active)
        checks.append(Check(f"Config '{active}'", _OK, "valid"))
    except InsyteError as exc:
        checks.append(Check(f"Config '{active}'", _FAIL, str(exc).splitlines()[0]))
        return checks

    env_name = config.database.url_env
    if database_url_is_available(config.database, active):
        checks.append(Check("Database URL", _OK, "available (env var or stored)"))
    else:
        checks.append(Check("Database URL", _WARN, f"{env_name} not set and none stored"))
    return checks


def doctor() -> None:
    """Run health checks and exit non-zero if any hard check fails."""

    console.print(f"[dim]insyte {__version__}[/dim]\n")

    checks: list[Check] = [
        _check_python(),
        *_check_packages(),
        _check_home(),
        *_check_active_project(),
    ]

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(width=2)
    table.add_column(style="bold")
    table.add_column(style="dim")
    for check in checks:
        table.add_row(_ICON[check.status], check.name, check.detail)
    console.print(table)

    failures = [c for c in checks if c.status == _FAIL]
    warnings = [c for c in checks if c.status == _WARN]
    if failures:
        console.print(f"\n[red]{len(failures)} check(s) failed.[/red]")
        raise typer.Exit(1)
    if warnings:
        console.print(f"\n[yellow]{len(warnings)} warning(s).[/yellow] Otherwise healthy.")
    else:
        console.print("\n[green]All checks passed.[/green]")
