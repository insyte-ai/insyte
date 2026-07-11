"""Filesystem layout for Insyte projects.

Everything lives under the Insyte *home* directory, which defaults to ``~/.insyte`` but can
be redirected with the ``INSYTE_HOME`` environment variable. The override keeps tests fully
isolated and lets users relocate their data.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from insyte.exceptions import InvalidProjectNameError

INSYTE_HOME_ENV = "INSYTE_HOME"
ACTIVE_PROJECT_FILE = "active_project"
PROJECT_SUBDIRS: tuple[str, ...] = ("logs", "cache", "exports")

_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def insyte_home() -> Path:
    """Return the Insyte home directory, honouring the ``INSYTE_HOME`` override."""

    override = os.environ.get(INSYTE_HOME_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".insyte"


def projects_root() -> Path:
    """Return the directory that contains all projects."""

    return insyte_home() / "projects"


def validate_project_name(name: str) -> str:
    """Validate a project name and return it, raising on anything unsafe.

    Names must be filesystem-safe single path segments so a project can never escape the
    projects directory (e.g. ``../evil`` or ``a/b`` are rejected).
    """

    if name in {".", ".."} or not _PROJECT_NAME_RE.match(name):
        raise InvalidProjectNameError(name)
    return name


def project_dir(name: str) -> Path:
    """Return the directory for a named project."""

    validate_project_name(name)
    return projects_root() / name


def config_path(name: str) -> Path:
    """Return the ``config.yaml`` path for a project."""

    return project_dir(name) / "config.yaml"


def semantic_path(name: str) -> Path:
    """Return the ``semantic.yaml`` path for a project."""

    return project_dir(name) / "semantic.yaml"


def metadata_path(name: str) -> Path:
    """Return the SQLite metadata path (created lazily by later milestones)."""

    return project_dir(name) / "metadata.sqlite"


def secret_path(name: str) -> Path:
    """Return the path to a project's stored database URL (a 0600 file, never config.yaml)."""

    return project_dir(name) / ".database_url"


def logs_dir(name: str) -> Path:
    """Return the logs directory for a project."""

    return project_dir(name) / "logs"


def cache_dir(name: str) -> Path:
    """Return the cache directory for a project."""

    return project_dir(name) / "cache"


def exports_dir(name: str) -> Path:
    """Return the exports directory for a project."""

    return project_dir(name) / "exports"


def _active_project_marker() -> Path:
    return insyte_home() / ACTIVE_PROJECT_FILE


def set_active_project(name: str) -> None:
    """Record the active project for commands such as ``insyte status``."""

    validate_project_name(name)
    home = insyte_home()
    home.mkdir(parents=True, exist_ok=True)
    _active_project_marker().write_text(name, encoding="utf-8")


def get_active_project() -> str | None:
    """Return the recorded active project name, or ``None`` if unset."""

    marker = _active_project_marker()
    if not marker.exists():
        return None
    value = marker.read_text(encoding="utf-8").strip()
    return value or None
