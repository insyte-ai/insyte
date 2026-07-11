"""Tests for path resolution and project-name validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from insyte.config import paths
from insyte.exceptions import InvalidProjectNameError


def test_home_honours_override(isolated_home: Path) -> None:
    assert paths.insyte_home() == isolated_home
    assert paths.projects_root() == isolated_home / "projects"


def test_home_falls_back_to_dot_insyte(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSYTE_HOME", raising=False)
    assert paths.insyte_home() == Path.home() / ".insyte"


def test_project_dir_layout(isolated_home: Path) -> None:
    assert paths.project_dir("demo") == isolated_home / "projects" / "demo"
    assert paths.config_path("demo").name == "config.yaml"
    assert paths.semantic_path("demo").name == "semantic.yaml"
    assert paths.metadata_path("demo").name == "metadata.sqlite"


@pytest.mark.parametrize("bad", ["../evil", "a/b", "", ".", "..", "has space", "/abs"])
def test_bad_project_names_rejected(bad: str) -> None:
    with pytest.raises(InvalidProjectNameError):
        paths.validate_project_name(bad)


@pytest.mark.parametrize("good", ["demo", "ecommerce-production", "proj_1", "a.b"])
def test_good_project_names_accepted(good: str) -> None:
    assert paths.validate_project_name(good) == good


def test_active_project_round_trip(isolated_home: Path) -> None:
    assert paths.get_active_project() is None
    paths.set_active_project("demo")
    assert paths.get_active_project() == "demo"
