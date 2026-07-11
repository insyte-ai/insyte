"""Tests for the CLI surface using Typer's CliRunner."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from insyte import __version__
from insyte.cli.app import app
from insyte.config import loader, paths

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_help_lists_all_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ["init", "status", "doctor", "connect", "scan", "chat", "serve", "mcp"]:
        assert command in result.stdout


def test_init_non_interactive_creates_project(isolated_home: Path) -> None:
    result = runner.invoke(
        app,
        [
            "init",
            "--name",
            "demo",
            "--db-url-env",
            "INSYTE_DATABASE_URL",
            "--schema",
            "public",
            "--analytics-mode",
            "direct",
            "--ai",
            "claude",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert loader.project_exists("demo")
    assert paths.get_active_project() == "demo"
    # The generated config must not contain a credential.
    assert "postgresql://" not in paths.config_path("demo").read_text(encoding="utf-8")


def test_init_stores_db_url_outside_config(isolated_home: Path) -> None:
    from insyte.config.models import DatabaseSection
    from insyte.config.secrets import database_url_is_available, stored_database_url

    url = "postgresql://reader:pw@localhost:5432/db"
    result = runner.invoke(app, ["init", "--name", "demo", "--db-url", url, "--yes"])
    assert result.exit_code == 0, result.stdout
    # The URL is stored in a separate file, NOT in config.yaml.
    assert "postgresql://" not in paths.config_path("demo").read_text(encoding="utf-8")
    assert stored_database_url("demo") == url
    # Resolvable from any process without an env var.
    assert database_url_is_available(DatabaseSection(), "demo") is True


def test_resolve_config_matches_active_project_case_insensitively(isolated_home: Path) -> None:
    # Mirrors the macOS case-insensitive-filesystem bug: folder is "Flipkart",
    # active-project marker says "flipkart".
    from insyte.cli._project import resolve_config
    from insyte.config.models import InsyteConfig, ProjectSection

    loader.create_project(InsyteConfig(project=ProjectSection(name="Flipkart")))
    paths.set_active_project("flipkart")  # different case than the folder
    config = resolve_config(None)
    assert config.project.name == "Flipkart"


def test_init_requires_name_when_non_interactive(isolated_home: Path) -> None:
    result = runner.invoke(app, ["init", "--yes"])
    assert result.exit_code == 1
    assert "name is required" in result.stdout.lower()


def test_status_renders_active_project(isolated_home: Path) -> None:
    runner.invoke(app, ["init", "--name", "demo", "--yes"])
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "demo" in result.stdout


def test_status_without_projects(isolated_home: Path) -> None:
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "no insyte projects" in result.stdout.lower()


def test_doctor_runs(isolated_home: Path) -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Python runtime" in result.stdout


def test_serve_stub_shows_milestone(isolated_home: Path) -> None:
    result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0
    assert "Milestone 7" in result.stdout
