"""Unit tests for audit persistence (query history + security events)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from insyte.metadata.models import ScanResult
from insyte.metadata.repository import MetadataRepository
from insyte.query.models import QueryHistoryEntry, SecurityEventEntry


@pytest.fixture
def repository(tmp_path: Path) -> MetadataRepository:
    repo = MetadataRepository(tmp_path / "metadata.sqlite")
    yield repo
    repo.dispose()


def test_record_and_list_query_history(repository: MetadataRepository) -> None:
    repository.record_query(
        QueryHistoryEntry(
            source="direct",
            raw_sql="SELECT 1",
            normalized_sql="SELECT 1 LIMIT 500",
            referenced_tables=["orders"],
            status="ok",
            row_count=1,
            duration_ms=12.3,
            applied_limit=500,
        )
    )
    history = repository.list_query_history()
    assert len(history) == 1
    assert history[0].status == "ok"
    assert history[0].referenced_tables == ["orders"]
    assert history[0].created_at is not None


def test_record_and_list_security_events(repository: MetadataRepository) -> None:
    repository.record_security_event(
        SecurityEventEntry(
            source="direct",
            event_type="blocked_query",
            raw_sql="DROP TABLE orders",
            violations=["Statement type 'DROP' is not allowed in a read-only query."],
        )
    )
    events = repository.list_security_events()
    assert len(events) == 1
    assert events[0].event_type == "blocked_query"
    assert events[0].violations


def test_history_ordered_newest_first(repository: MetadataRepository) -> None:
    for i in range(3):
        repository.record_query(
            QueryHistoryEntry(
                source="direct",
                raw_sql=f"SELECT {i}",
                normalized_sql=None,
                referenced_tables=[],
                status="ok",
            )
        )
    history = repository.list_query_history(limit=2)
    assert len(history) == 2
    assert history[0].raw_sql == "SELECT 2"  # newest first


def test_audit_survives_rescan(repository: MetadataRepository) -> None:
    repository.record_query(
        QueryHistoryEntry(
            source="direct",
            raw_sql="SELECT 1",
            normalized_sql=None,
            referenced_tables=[],
            status="ok",
        )
    )
    now = datetime.now(UTC)
    repository.save_scan(
        ScanResult(schemas={"public": None}, tables=[], relationships=[]),
        started_at=now,
        finished_at=now,
    )
    # A re-scan replaces structural tables but must not wipe the audit log.
    assert len(repository.list_query_history()) == 1
