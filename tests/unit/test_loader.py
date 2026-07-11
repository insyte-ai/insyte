"""Tests for the YAML config loader and project creation."""

from __future__ import annotations

from pathlib import Path

import pytest

from insyte.config import loader, paths
from insyte.config.models import (
    DatabaseSection,
    InsyteConfig,
    ProjectSection,
)
from insyte.exceptions import ProjectExistsError, ProjectNotFoundError


def _config(name: str = "demo") -> InsyteConfig:
    return InsyteConfig(
        project=ProjectSection(name=name),
        database=DatabaseSection(
            url_env="MY_DB_URL",
            allowed_schemas=["public", "sales"],
            blocked_columns=["users.password_hash"],
        ),
    )


def test_create_project_writes_full_layout(isolated_home: Path) -> None:
    loader.create_project(_config())
    pdir = paths.project_dir("demo")
    assert (pdir / "config.yaml").exists()
    assert (pdir / "semantic.yaml").exists()
    for sub in paths.PROJECT_SUBDIRS:
        assert (pdir / sub).is_dir()
    assert paths.get_active_project() == "demo"


def test_round_trip_preserves_values(isolated_home: Path) -> None:
    original = _config()
    loader.create_project(original)
    loaded = loader.load_config("demo")
    assert loaded == original
    assert loaded.database.allowed_schemas == ["public", "sales"]


def test_list_projects(isolated_home: Path) -> None:
    loader.create_project(_config("alpha"))
    loader.create_project(_config("beta"))
    assert loader.list_projects() == ["alpha", "beta"]


def test_load_missing_project_raises(isolated_home: Path) -> None:
    with pytest.raises(ProjectNotFoundError):
        loader.load_config("ghost")


def test_create_existing_without_force_raises(isolated_home: Path) -> None:
    loader.create_project(_config())
    with pytest.raises(ProjectExistsError):
        loader.create_project(_config())


def test_create_existing_with_force_overwrites(isolated_home: Path) -> None:
    loader.create_project(_config())
    loader.create_project(_config(), force=True)  # must not raise


def test_config_yaml_never_contains_a_password(isolated_home: Path) -> None:
    loader.create_project(_config())
    text = paths.config_path("demo").read_text(encoding="utf-8").lower()
    assert "password" not in text.replace("password_hash", "")
    assert "postgresql://" not in text
    # Only the env var *name* is stored.
    assert "my_db_url" in text
