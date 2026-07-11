"""Unit tests for the PostgreSQL connector's pure logic (no live database)."""

from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url

from insyte.config.models import DatabaseSection, QuerySection, SSLMode
from insyte.connectors.postgres import (
    APPLICATION_NAME,
    CONNECT_TIMEOUT_SECONDS,
    build_connect_args,
    build_engine,
    interpret_permissions,
    normalize_postgres_url,
    read_only_setup_statements,
)
from insyte.exceptions import DatabaseConnectionError, UnsupportedDatabaseError

_RAW = "postgresql://reader:pw@db.internal:5432/app_db"


def test_normalize_forces_psycopg_driver() -> None:
    url = normalize_postgres_url(_RAW)
    assert url.drivername == "postgresql+psycopg"
    assert url.host == "db.internal"
    assert url.port == 5432
    assert url.database == "app_db"


def test_normalize_accepts_postgres_scheme() -> None:
    url = normalize_postgres_url("postgres://reader:pw@localhost/app")
    assert url.drivername == "postgresql+psycopg"


def test_normalize_rejects_non_postgres() -> None:
    with pytest.raises(UnsupportedDatabaseError):
        normalize_postgres_url("mysql://root:pw@localhost/app")


def test_normalize_rejects_malformed() -> None:
    with pytest.raises(DatabaseConnectionError):
        normalize_postgres_url("not a url at all ::://")


def test_connect_args_defaults() -> None:
    url = make_url(_RAW)
    args = build_connect_args(DatabaseSection(ssl_mode=SSLMode.require), url)
    assert args["connect_timeout"] == CONNECT_TIMEOUT_SECONDS
    assert args["application_name"] == APPLICATION_NAME
    assert args["sslmode"] == "require"


def test_connect_args_respect_url_sslmode() -> None:
    url = make_url(_RAW + "?sslmode=disable")
    args = build_connect_args(DatabaseSection(ssl_mode=SSLMode.require), url)
    # An explicit URL sslmode must win — do not override it.
    assert "sslmode" not in args


def test_build_engine_pool_and_driver() -> None:
    url = normalize_postgres_url(_RAW)
    engine = build_engine(url, build_connect_args(DatabaseSection(), url))
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.pool.size() == 2
        # Never leak the password via the engine's string form.
        assert "pw" not in str(engine.url)
    finally:
        engine.dispose()


def test_read_only_setup_statements_order_and_values() -> None:
    stmts = read_only_setup_statements(QuerySection(timeout_seconds=20, lock_timeout_seconds=3))
    assert stmts[0] == "SET TRANSACTION READ ONLY"
    assert "statement_timeout = '20s'" in stmts[1]
    assert "lock_timeout = '3s'" in stmts[2]
    assert "idle_in_transaction_session_timeout = '30s'" in stmts[3]


def test_interpret_permissions_read_only() -> None:
    report = interpret_permissions((False, False, False), [])
    assert report.is_superuser is False
    assert report.has_write_privileges is False
    assert report.has_write_access is False


def test_interpret_permissions_superuser() -> None:
    report = interpret_permissions((True, True, True), [])
    assert report.is_superuser is True
    assert report.has_write_access is True


def test_interpret_permissions_write_grants() -> None:
    report = interpret_permissions(
        (False, False, False),
        [("public", "orders", "INSERT"), ("public", "orders", "UPDATE")],
    )
    assert report.has_write_privileges is True
    assert report.has_write_access is True
    assert "public.orders (INSERT)" in report.write_samples
