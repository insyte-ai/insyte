"""Unit tests for the ``insyte studio`` command helpers."""

from __future__ import annotations

import socket

import pytest
from typer.testing import CliRunner

from insyte.cli.app import app
from insyte.cli.studio_command import find_available_port
from insyte.exceptions import InsyteError

runner = CliRunner()


def test_find_available_port_returns_free_port() -> None:
    port = find_available_port(3838, "127.0.0.1")
    # It must be actually bindable.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


def test_find_available_port_skips_taken_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken_port = taken.getsockname()[1]
        found = find_available_port(taken_port, "127.0.0.1")
        assert found != taken_port


def test_find_available_port_exhausted() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken_port = taken.getsockname()[1]
        with pytest.raises(InsyteError):
            find_available_port(taken_port, "127.0.0.1", attempts=1)


def test_studio_help_lists_flags() -> None:
    result = runner.invoke(app, ["studio", "--help"])
    assert result.exit_code == 0
    for flag in ["--project", "--host", "--port", "--no-browser", "--reload"]:
        assert flag in result.stdout


def test_studio_no_project_opens_setup(monkeypatch, isolated_home) -> None:
    launched = []
    monkeypatch.setattr(
        "insyte.cli.studio_command.uvicorn.run",
        lambda app, **kwargs: launched.append(app),
    )
    monkeypatch.setattr("insyte.cli.studio_command.find_available_port", lambda *_args: 3838)
    result = runner.invoke(app, ["studio", "--no-browser"])
    assert result.exit_code == 0
    assert "opening browser setup" in result.stdout
    assert launched
