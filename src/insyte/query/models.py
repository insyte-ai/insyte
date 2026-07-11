"""Data structures for the safe SQL pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class QueryValidationResult:
    """Outcome of validating a query against the safety rules."""

    valid: bool
    normalized_sql: str | None
    violations: list[str]
    referenced_tables: list[str]
    referenced_columns: list[str]
    applied_limit: int | None


@dataclass
class ExecutionResult:
    """Rows and metadata returned by a successful safe query."""

    columns: list[str]
    rows: list[tuple[object, ...]]
    row_count: int
    truncated: bool
    duration_ms: float
    applied_limit: int | None
    normalized_sql: str
    referenced_tables: list[str]


@dataclass
class QueryHistoryEntry:
    """An audited query attempt (written on every execution, success or failure)."""

    source: str
    raw_sql: str
    normalized_sql: str | None
    referenced_tables: list[str]
    status: str  # ok | blocked | error
    row_count: int | None = None
    duration_ms: float | None = None
    applied_limit: int | None = None
    error: str | None = None
    created_at: datetime | None = None


@dataclass
class SecurityEventEntry:
    """A security-relevant event, e.g. a blocked query."""

    source: str
    event_type: str
    raw_sql: str | None
    violations: list[str] = field(default_factory=list)
    created_at: datetime | None = None


class AuditRecorder(Protocol):
    """Sink for audit records. Implemented by the metadata repository.

    Execution must never proceed without an audit sink, so this is a required dependency of
    the executor rather than an optional one.
    """

    def record_query(self, entry: QueryHistoryEntry) -> None: ...

    def record_security_event(self, event: SecurityEventEntry) -> None: ...
