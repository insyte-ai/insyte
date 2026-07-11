"""Shared pytest fixtures.

Every test runs against an isolated Insyte home directory so nothing touches the real
``~/.insyte``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point ``INSYTE_HOME`` at a temporary directory for the duration of each test."""

    home = tmp_path / "insyte_home"
    home.mkdir()
    monkeypatch.setenv("INSYTE_HOME", str(home))
    # Ensure a stray real env var never leaks into tests unless a test sets it.
    monkeypatch.delenv("INSYTE_DATABASE_URL", raising=False)
    yield home
