"""Install / uninstall the Insyte MCP server into AI client configs.

Supports Claude Code (JSON, ``~/.claude.json``) and Codex (TOML, ``~/.codex/config.toml``).
The installer preserves any existing configuration and only touches the ``insyte`` entry.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import tomli_w

from insyte.exceptions import InsyteError

ENTRY_NAME = "insyte"


class ConfigFormat(StrEnum):
    json = "json"
    toml = "toml"


@dataclass
class ClientTarget:
    name: str
    config_path: Path
    fmt: ConfigFormat
    servers_key: str


class UnknownClientError(InsyteError):
    """Raised when an unsupported MCP client is requested."""


def client_target(client: str, *, home: Path | None = None) -> ClientTarget:
    """Return the config target for a supported client ('claude' or 'codex')."""

    home = home or Path.home()
    normalized = client.lower()
    if normalized == "claude":
        return ClientTarget("claude", home / ".claude.json", ConfigFormat.json, "mcpServers")
    if normalized == "codex":
        return ClientTarget(
            "codex", home / ".codex" / "config.toml", ConfigFormat.toml, "mcp_servers"
        )
    raise UnknownClientError(f"Unsupported MCP client '{client}'. Use 'claude' or 'codex'.")


def build_server_entry(
    project: str,
    *,
    insyte_home: str | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Build the MCP server entry that launches ``insyte mcp start``.

    ``database_url`` is only included when the caller explicitly opts in; it is stored in the
    client's local config file (which the AI model never reads), never sent to the model.
    """

    entry: dict[str, Any] = {
        "command": "insyte",
        "args": ["mcp", "start", "--project", project],
    }
    env: dict[str, str] = {}
    if insyte_home:
        env["INSYTE_HOME"] = insyte_home
    if database_url:
        env["INSYTE_DATABASE_URL"] = database_url
    if env:
        entry["env"] = env
    return entry


def load_config(target: ClientTarget) -> dict[str, Any]:
    """Load a client config file, returning an empty dict if it does not exist."""

    if not target.config_path.exists():
        return {}
    text = target.config_path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    loaded = json.loads(text) if target.fmt is ConfigFormat.json else tomllib.loads(text)
    if not isinstance(loaded, dict):
        raise InsyteError(f"Unexpected config format in {target.config_path}.")
    return loaded


def save_config(target: ClientTarget, data: dict[str, Any]) -> None:
    target.config_path.parent.mkdir(parents=True, exist_ok=True)
    if target.fmt is ConfigFormat.json:
        target.config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    else:
        target.config_path.write_text(tomli_w.dumps(data), encoding="utf-8")


def install(target: ClientTarget, entry: dict[str, Any]) -> dict[str, Any]:
    """Merge the Insyte entry into the client config, preserving everything else."""

    config = load_config(target)
    servers = config.get(target.servers_key)
    if not isinstance(servers, dict):
        servers = {}
    servers[ENTRY_NAME] = entry
    config[target.servers_key] = servers
    save_config(target, config)
    return config


def uninstall(target: ClientTarget) -> bool:
    """Remove the Insyte entry. Returns True if an entry was removed."""

    config = load_config(target)
    servers = config.get(target.servers_key)
    if not isinstance(servers, dict) or ENTRY_NAME not in servers:
        return False
    del servers[ENTRY_NAME]
    config[target.servers_key] = servers
    save_config(target, config)
    return True


def is_installed(target: ClientTarget) -> bool:
    servers = load_config(target).get(target.servers_key)
    return isinstance(servers, dict) and ENTRY_NAME in servers
