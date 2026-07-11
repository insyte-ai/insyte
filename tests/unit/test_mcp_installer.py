"""Unit tests for the MCP client installer."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from insyte.exceptions import InsyteError
from insyte.mcp.installer import (
    build_server_entry,
    client_target,
    install,
    is_installed,
    load_config,
    uninstall,
)


def test_build_entry_minimal() -> None:
    entry = build_server_entry("shop")
    assert entry["command"] == "insyte"
    assert entry["args"] == ["mcp", "start", "--project", "shop"]
    assert "env" not in entry  # nothing to embed


def test_build_entry_with_env() -> None:
    entry = build_server_entry("shop", insyte_home="/home/x/.insyte", database_url="postgresql://x")
    assert entry["env"]["INSYTE_HOME"] == "/home/x/.insyte"
    assert entry["env"]["INSYTE_DATABASE_URL"] == "postgresql://x"


def test_unknown_client() -> None:
    with pytest.raises(InsyteError):
        client_target("emacs", home=Path("/tmp"))


def test_claude_install_preserves_existing(tmp_path: Path) -> None:
    target = client_target("claude", home=tmp_path)
    target.config_path.write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"})
    )
    install(target, build_server_entry("shop"))

    data = json.loads(target.config_path.read_text())
    assert data["theme"] == "dark"  # preserved
    assert "other" in data["mcpServers"]  # preserved
    assert data["mcpServers"]["insyte"]["command"] == "insyte"
    assert is_installed(target)


def test_claude_uninstall(tmp_path: Path) -> None:
    target = client_target("claude", home=tmp_path)
    install(target, build_server_entry("shop"))
    assert uninstall(target) is True
    assert not is_installed(target)
    assert uninstall(target) is False  # already gone


def test_codex_install_toml(tmp_path: Path) -> None:
    target = client_target("codex", home=tmp_path)
    target.config_path.parent.mkdir(parents=True)
    target.config_path.write_text('model = "gpt-5"\n\n[mcp_servers.other]\ncommand = "y"\n')
    install(target, build_server_entry("shop", database_url="postgresql://x"))

    data = tomllib.loads(target.config_path.read_text())
    assert data["model"] == "gpt-5"  # preserved
    assert data["mcp_servers"]["other"]["command"] == "y"  # preserved
    assert data["mcp_servers"]["insyte"]["args"] == ["mcp", "start", "--project", "shop"]
    assert data["mcp_servers"]["insyte"]["env"]["INSYTE_DATABASE_URL"] == "postgresql://x"


def test_load_missing_config(tmp_path: Path) -> None:
    target = client_target("claude", home=tmp_path)
    assert load_config(target) == {}
