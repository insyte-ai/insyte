"""Tests for runtime secret resolution."""

from __future__ import annotations

import os
import stat

import pytest

from insyte.config import paths
from insyte.config.models import DatabaseSection
from insyte.config.secrets import (
    database_url_is_available,
    delete_stored_database_url,
    resolve_database_url,
    store_database_url,
    stored_database_url,
)
from insyte.exceptions import SecretResolutionError

_URL = "postgresql://insyte_reader:s3cret@localhost:5432/app_db"


def test_resolves_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_DB_URL", _URL)
    db = DatabaseSection(url_env="MY_DB_URL")
    assert resolve_database_url(db) == _URL
    assert database_url_is_available(db) is True


def test_missing_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_DB_URL", raising=False)
    db = DatabaseSection(url_env="MY_DB_URL")
    assert database_url_is_available(db) is False
    with pytest.raises(SecretResolutionError):
        resolve_database_url(db)


def test_error_message_contains_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_DB_URL", raising=False)
    db = DatabaseSection(url_env="MY_DB_URL")
    try:
        resolve_database_url(db)
    except SecretResolutionError as exc:
        assert "s3cret" not in str(exc)


def test_store_read_delete(isolated_home, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_DB_URL", raising=False)
    store_database_url("proj", _URL)
    assert stored_database_url("proj") == _URL
    # 0600 permissions — owner read/write only.
    mode = stat.S_IMODE(os.stat(paths.secret_path("proj")).st_mode)
    assert mode == 0o600
    assert delete_stored_database_url("proj") is True
    assert stored_database_url("proj") is None
    assert delete_stored_database_url("proj") is False


def test_resolve_falls_back_to_stored(isolated_home, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MY_DB_URL", raising=False)
    store_database_url("proj", _URL)
    db = DatabaseSection(url_env="MY_DB_URL")
    assert resolve_database_url(db, "proj") == _URL  # env unset → uses stored
    assert database_url_is_available(db, "proj") is True
    # Without the project name it can't find the stored secret.
    assert database_url_is_available(db) is False


def test_env_takes_precedence_over_stored(isolated_home, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_DB_URL", "postgresql://from-env/db")
    store_database_url("proj", _URL)
    db = DatabaseSection(url_env="MY_DB_URL")
    assert resolve_database_url(db, "proj") == "postgresql://from-env/db"
