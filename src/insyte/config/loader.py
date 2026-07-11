"""Load and persist Insyte project configuration as YAML.

This module is the single place that reads and writes ``config.yaml``. It guarantees the
project directory layout exists and that no credential is ever serialised to disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from insyte.config import paths
from insyte.config.models import InsyteConfig
from insyte.exceptions import ConfigError, ProjectExistsError, ProjectNotFoundError

_SEMANTIC_SCAFFOLD: dict[str, Any] = {"entities": {}, "metrics": {}, "dimensions": {}}


def project_exists(name: str) -> bool:
    """Return whether a project with a ``config.yaml`` exists."""

    return paths.config_path(name).exists()


def list_projects() -> list[str]:
    """Return the names of all projects, sorted."""

    root = paths.projects_root()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "config.yaml").exists())


def ensure_project_dirs(name: str) -> Path:
    """Create the project directory and its subdirectories, returning the project dir."""

    pdir = paths.project_dir(name)
    pdir.mkdir(parents=True, exist_ok=True)
    for sub in paths.PROJECT_SUBDIRS:
        (pdir / sub).mkdir(exist_ok=True)
    return pdir


def save_config(config: InsyteConfig) -> Path:
    """Write ``config.yaml`` for a project, creating directories as needed."""

    name = config.project.name
    ensure_project_dirs(name)
    cpath = paths.config_path(name)
    data = config.to_yaml_dict()
    cpath.write_text(
        yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )
    return cpath


def load_config(name: str) -> InsyteConfig:
    """Load and validate a project's ``config.yaml``."""

    cpath = paths.config_path(name)
    if not cpath.exists():
        raise ProjectNotFoundError(name)
    raw = yaml.safe_load(cpath.read_text(encoding="utf-8")) or {}
    try:
        return InsyteConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration in {cpath}:\n{exc}") from exc


def create_project(config: InsyteConfig, *, force: bool = False) -> Path:
    """Create a new project on disk and mark it active.

    Writes ``config.yaml`` plus an empty ``semantic.yaml`` scaffold. The ``metadata.sqlite``
    and ``analytics.duckdb`` files are created lazily by later milestones. Raises
    :class:`ProjectExistsError` if the project exists and ``force`` is not set.
    """

    name = config.project.name
    if project_exists(name) and not force:
        raise ProjectExistsError(name)

    cpath = save_config(config)

    spath = paths.semantic_path(name)
    if not spath.exists():
        spath.write_text(
            yaml.safe_dump(_SEMANTIC_SCAFFOLD, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    paths.set_active_project(name)
    return cpath
