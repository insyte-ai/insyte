"""Integration test: scan the ecommerce fixture against a real PostgreSQL server.

Runs only when ``INSYTE_TEST_DATABASE_URL`` is set and the account can create the fixture
schema (DDL). Skipped otherwise.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from helpers import load_ecommerce_fixture

from insyte.config.models import DatabaseSection, QuerySection, SSLMode
from insyte.connectors.postgres import PostgresConnector
from insyte.metadata.models import RelationshipKind, TableCategory, TableKind
from insyte.metadata.scanner import SchemaScanner

_TEST_URL = os.environ.get("INSYTE_TEST_DATABASE_URL")
_FIXTURE = Path(__file__).parent.parent / "fixtures" / "ecommerce.sql"

pytestmark = pytest.mark.skipif(
    not _TEST_URL, reason="Set INSYTE_TEST_DATABASE_URL to run PostgreSQL integration tests."
)


@pytest.fixture(scope="module")
def scanned():
    assert _TEST_URL is not None
    load_ecommerce_fixture(_TEST_URL, _FIXTURE)

    connector = PostgresConnector(
        _TEST_URL, DatabaseSection(ssl_mode=SSLMode.prefer), QuerySection()
    )
    result = SchemaScanner(connector, DatabaseSection(ssl_mode=SSLMode.prefer)).scan()
    connector.dispose()
    return result


def test_all_fixture_tables_found(scanned) -> None:
    names = {t.name for t in scanned.tables if t.kind is TableKind.table}
    assert {
        "cities",
        "customers",
        "products",
        "orders",
        "order_items",
        "payments",
        "refunds",
    } <= names


def test_declared_foreign_keys_detected(scanned) -> None:
    fks = [r for r in scanned.relationships if r.kind is RelationshipKind.foreign_key]
    pairs = {(r.source_table, r.target_table) for r in fks}
    assert ("orders", "customers") in pairs
    assert ("order_items", "orders") in pairs
    assert ("order_items", "products") in pairs
    assert ("refunds", "payments") in pairs


def test_row_estimates_and_comments(scanned) -> None:
    customers = next(t for t in scanned.tables if t.name == "customers")
    assert customers.row_estimate is not None
    email = next(c for c in customers.columns if c.name == "email")
    assert email.is_unique is True


def test_classifications(scanned) -> None:
    by_name = {t.name: t for t in scanned.tables}
    assert by_name["order_items"].category is TableCategory.bridge
    assert by_name["orders"].category is TableCategory.fact
    assert by_name["cities"].category is TableCategory.dimension


def test_blocked_column_is_excluded() -> None:
    assert _TEST_URL is not None
    db = DatabaseSection(ssl_mode=SSLMode.prefer, blocked_columns=["customers.email"])
    connector = PostgresConnector(_TEST_URL, db, QuerySection())
    result = SchemaScanner(connector, db).scan()
    connector.dispose()
    customers = next(t for t in result.tables if t.name == "customers")
    assert "email" not in {c.name for c in customers.columns}
