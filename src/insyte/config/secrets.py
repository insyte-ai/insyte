"""Runtime resolution of database credentials.

The connection URL is resolved only when Insyte actually needs to connect. It is **never**
written to ``config.yaml``, never logged, and never returned to an AI client.

There are two sources, tried in order:

1. The environment variable named by ``database.url_env`` (best for CI / shared machines).
2. A per-project stored URL — a ``0600`` file (``~/.insyte/projects/<name>/.database_url``)
   that ``insyte init`` writes when you paste the URL during setup. This lets every terminal,
   the TUI, and the MCP server connect without re-exporting anything, while keeping the secret
   out of ``config.yaml`` (which stays safe to share).
"""

from __future__ import annotations

import os

from insyte.config import paths
from insyte.config.models import DatabaseSection
from insyte.exceptions import SecretResolutionError


def store_database_url(project_name: str, url: str) -> None:
    """Persist a project's database URL to a 0600 file (never to config.yaml)."""

    path = paths.secret_path(project_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(url.strip(), encoding="utf-8")
    os.chmod(path, 0o600)


def stored_database_url(project_name: str) -> str | None:
    """Return the project's stored URL, if any."""

    path = paths.secret_path(project_name)
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def delete_stored_database_url(project_name: str) -> bool:
    """Remove a stored URL. Returns True if one existed."""

    path = paths.secret_path(project_name)
    if path.exists():
        path.unlink()
        return True
    return False


def resolve_database_url(database: DatabaseSection, project_name: str | None = None) -> str:
    """Resolve the database URL from the environment or the project's stored secret.

    Raises :class:`SecretResolutionError` (with no secret material in the message) when neither
    source has a value.
    """

    env_name = database.url_env
    if env_name:
        value = os.environ.get(env_name)
        if value:
            return value

    if project_name is not None:
        stored = stored_database_url(project_name)
        if stored:
            return stored

    raise SecretResolutionError(
        f"No database URL found. Set the {env_name!r} environment variable, or run "
        "'insyte init' and paste the URL to store it locally."
    )


def database_url_is_available(database: DatabaseSection, project_name: str | None = None) -> bool:
    """Return whether a URL can be resolved (env var set or a stored secret exists)."""

    if database.url_env and os.environ.get(database.url_env):
        return True
    return project_name is not None and stored_database_url(project_name) is not None
