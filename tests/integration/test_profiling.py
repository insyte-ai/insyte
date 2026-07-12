"""Integration test: safe profiling + semantic generation against real PostgreSQL."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from helpers import load_ecommerce_fixture

from insyte.config.models import (
    DatabaseSection,
    InsyteConfig,
    ProfilingSection,
    ProjectSection,
    SSLMode,
)
from insyte.connectors.postgres import PostgresConnector
from insyte.metadata.profiler import Profiler
from insyte.metadata.repository import MetadataRepository, utcnow
from insyte.metadata.scanner import SchemaScanner
from insyte.semantic.generator import generate_semantic
from insyte.semantic.models import SemanticLayer
from insyte.semantic.validator import SchemaIndex, validate_semantic

_TEST_URL = os.environ.get("INSYTE_TEST_DATABASE_URL")
_FIXTURE = Path(__file__).parent.parent / "fixtures" / "ecommerce.sql"

pytestmark = pytest.mark.skipif(
    not _TEST_URL, reason="Set INSYTE_TEST_DATABASE_URL to run PostgreSQL integration tests."
)


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory):
    assert _TEST_URL is not None
    load_ecommerce_fixture(_TEST_URL, _FIXTURE)

    config = InsyteConfig(
        project=ProjectSection(name="it"),
        database=DatabaseSection(ssl_mode=SSLMode.prefer),
    )
    connector = PostgresConnector(_TEST_URL, config.database, config.query)
    metadata = MetadataRepository(tmp_path_factory.mktemp("meta") / "metadata.sqlite")
    metadata.save_scan(
        SchemaScanner(connector, config.database).scan(), started_at=utcnow(), finished_at=utcnow()
    )
    profiler = Profiler(connector, metadata, ProfilingSection(sample_rows=1000))
    metadata.save_profiles(profiler.profile())
    yield metadata
    connector.dispose()
    metadata.dispose()


def test_profiles_created_and_email_masked(repo: MetadataRepository) -> None:
    profiles = {p.qualified_column: p for p in repo.list_column_profiles()}
    email = profiles["public.customers.email"]
    assert email.is_pii is True
    assert email.pii_type == "email"
    # No masked sample value reveals the domain.
    assert all("example" not in value for value, _ in email.top_values)

    status = profiles["public.orders.status"]
    assert status.is_pii is False
    assert status.distinct_estimate >= 1


def test_generated_semantic_is_valid(repo: MetadataRepository) -> None:
    details = [
        detail
        for s in repo.list_tables()
        if (detail := repo.get_table(s.schema, s.name)) is not None
    ]
    profiles = {p.qualified_column: p for p in repo.list_column_profiles()}
    result = generate_semantic(details, profiles, SemanticLayer())
    assert result.added_metrics  # at least the fact-table count + sums

    issues = validate_semantic(result.layer, SchemaIndex.from_repository(repo))
    errors = [i for i in issues if i.level == "error"]
    assert errors == []  # generated layer validates cleanly
