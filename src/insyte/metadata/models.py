"""Data structures for schema metadata.

This module holds three kinds of thing, and deliberately no logic, so the scanner,
relationship detector, classifier and repository can all import it without cycles:

* **Enums** describing schema shape.
* **Scanned\\* dataclasses** — the in-memory result the scanner produces (write model).
* **SQLAlchemy ORM records** — how metadata is persisted in the per-project SQLite database,
  plus small read dataclasses returned by the repository.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# --------------------------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------------------------


class TableKind(StrEnum):
    """Relational object kind."""

    table = "table"
    view = "view"


class RelationshipKind(StrEnum):
    """How confident we are that a relationship is real."""

    foreign_key = "foreign_key"  # declared in the database — certain
    inferred = "inferred"  # deduced from naming/types/uniqueness — a suggestion


class TableCategory(StrEnum):
    """Deterministic analytical classification of a table."""

    fact = "fact"
    dimension = "dimension"
    bridge = "bridge"
    event = "event"
    snapshot = "snapshot"
    configuration = "configuration"
    unknown = "unknown"


class CardinalityCategory(StrEnum):
    """Coarse description of how many distinct values a column holds."""

    constant = "constant"
    unique = "unique"
    high = "high"
    medium = "medium"
    low = "low"
    empty = "empty"


# --------------------------------------------------------------------------------------------
# Scanned* dataclasses (write model produced by the scanner)
# --------------------------------------------------------------------------------------------


@dataclass
class ScannedColumn:
    name: str
    ordinal: int
    data_type: str
    nullable: bool
    default: str | None = None
    comment: str | None = None
    is_primary_key: bool = False
    is_unique: bool = False


@dataclass
class ScannedIndex:
    name: str
    columns: list[str]
    is_unique: bool
    is_primary: bool


@dataclass
class ScannedForeignKey:
    name: str | None
    columns: list[str]
    target_schema: str
    target_table: str
    target_columns: list[str]


@dataclass
class ScannedTable:
    schema: str
    name: str
    kind: TableKind
    columns: list[ScannedColumn]
    primary_key_columns: list[str] = field(default_factory=list)
    foreign_keys: list[ScannedForeignKey] = field(default_factory=list)
    indexes: list[ScannedIndex] = field(default_factory=list)
    comment: str | None = None
    row_estimate: int | None = None
    size_bytes: int | None = None
    category: TableCategory = TableCategory.unknown
    category_confidence: float = 0.0

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass
class Relationship:
    source_schema: str
    source_table: str
    source_columns: list[str]
    target_schema: str
    target_table: str
    target_columns: list[str]
    kind: RelationshipKind
    confidence: float
    constraint_name: str | None = None


@dataclass
class ScanResult:
    schemas: dict[str, str | None]  # schema name -> comment
    tables: list[ScannedTable]
    relationships: list[Relationship]
    server_version: str | None = None


@dataclass
class ColumnProfile:
    """A single column's safe profile. Sample-derived values are masked when PII."""

    schema: str
    table: str
    column: str
    null_fraction: float
    distinct_estimate: int
    duplicate_ratio: float
    cardinality: CardinalityCategory
    sampled_rows: int
    min_value: str | None = None
    max_value: str | None = None
    avg_value: float | None = None
    top_values: list[tuple[str, int]] = field(default_factory=list)
    is_pii: bool = False
    pii_type: str | None = None
    pii_confidence: float = 0.0

    @property
    def qualified_column(self) -> str:
        return f"{self.schema}.{self.table}.{self.column}"


@dataclass
class TableProfile:
    schema: str
    table: str
    row_estimate: int | None
    sampled_rows: int
    column_count: int


@dataclass
class ProfileResult:
    table_profiles: list[TableProfile]
    column_profiles: list[ColumnProfile]


@dataclass
class SyncState:
    """The sync state of one table copied into the local DuckDB."""

    table: str  # schema.table
    cursor_column: str | None
    cursor_kind: str | None  # timestamp | integer
    last_cursor: str | None
    status: str  # completed | failed
    row_count: int
    mode: str  # full | incremental


@dataclass
class Conversation:
    """A Studio conversation (persisted in the metadata database)."""

    id: str
    project: str
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass
class ConversationMessage:
    id: int
    conversation_id: str
    role: str  # user | assistant
    content: str
    analysis_id: str | None
    created_at: datetime


@dataclass
class ConversationContextSnapshot:
    id: int
    conversation_id: str
    analysis_id: str | None
    context_json: dict
    created_at: datetime


@dataclass
class SavedInvestigation:
    """A completed Studio investigation saved for later reading."""

    id: str
    project: str
    analysis_id: str
    conversation_id: str | None
    title: str
    summary: str
    question: str
    result_json: dict
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------------------------
# ORM records (persistence model)
# --------------------------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


class SchemaRecord(Base):
    __tablename__ = "schemas"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    comment: Mapped[str | None] = mapped_column(default=None)


class TableRecord(Base):
    __tablename__ = "tables"
    __table_args__ = (UniqueConstraint("schema_name", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    schema_id: Mapped[int] = mapped_column(ForeignKey("schemas.id", ondelete="CASCADE"))
    schema_name: Mapped[str]
    name: Mapped[str]
    kind: Mapped[str]
    comment: Mapped[str | None] = mapped_column(default=None)
    row_estimate: Mapped[int | None] = mapped_column(default=None)
    size_bytes: Mapped[int | None] = mapped_column(default=None)
    column_count: Mapped[int] = mapped_column(default=0)
    category: Mapped[str] = mapped_column(default=TableCategory.unknown.value)
    category_confidence: Mapped[float] = mapped_column(default=0.0)


class ColumnRecord(Base):
    __tablename__ = "columns"

    id: Mapped[int] = mapped_column(primary_key=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("tables.id", ondelete="CASCADE"))
    schema_name: Mapped[str]
    table_name: Mapped[str]
    name: Mapped[str]
    ordinal: Mapped[int]
    data_type: Mapped[str]
    nullable: Mapped[bool]
    default: Mapped[str | None] = mapped_column(default=None)
    is_primary_key: Mapped[bool] = mapped_column(default=False)
    is_unique: Mapped[bool] = mapped_column(default=False)
    comment: Mapped[str | None] = mapped_column(default=None)


class IndexRecord(Base):
    __tablename__ = "indexes"

    id: Mapped[int] = mapped_column(primary_key=True)
    table_id: Mapped[int] = mapped_column(ForeignKey("tables.id", ondelete="CASCADE"))
    schema_name: Mapped[str]
    table_name: Mapped[str]
    name: Mapped[str]
    columns: Mapped[list[str]] = mapped_column(JSON)
    is_unique: Mapped[bool] = mapped_column(default=False)
    is_primary: Mapped[bool] = mapped_column(default=False)


class RelationshipRecord(Base):
    __tablename__ = "relationships"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_schema: Mapped[str]
    source_table: Mapped[str]
    source_columns: Mapped[list[str]] = mapped_column(JSON)
    target_schema: Mapped[str]
    target_table: Mapped[str]
    target_columns: Mapped[list[str]] = mapped_column(JSON)
    kind: Mapped[str]
    confidence: Mapped[float]
    constraint_name: Mapped[str | None] = mapped_column(default=None)


class ScanRun(Base):
    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime]
    server_version: Mapped[str | None] = mapped_column(default=None)
    schema_count: Mapped[int] = mapped_column(default=0)
    table_count: Mapped[int] = mapped_column(default=0)
    view_count: Mapped[int] = mapped_column(default=0)
    column_count: Mapped[int] = mapped_column(default=0)
    relationship_count: Mapped[int] = mapped_column(default=0)


class MetadataStateRecord(Base):
    """Small versioned values that connect independently refreshed metadata."""

    __tablename__ = "metadata_state"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]


class SearchDocumentRecord(Base):
    """A safe local search document derived only from scanned metadata."""

    __tablename__ = "search_documents"
    __table_args__ = (UniqueConstraint("object_type", "object_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    object_type: Mapped[str]
    object_id: Mapped[str]
    title: Mapped[str]
    content: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class QueryHistoryRecord(Base):
    """One audited query attempt. Survives re-scans (not a structural table)."""

    __tablename__ = "query_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime]
    source: Mapped[str]  # direct | mcp | orchestrator
    raw_sql: Mapped[str]
    normalized_sql: Mapped[str | None] = mapped_column(default=None)
    referenced_tables: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(default="ok")  # ok | blocked | error
    row_count: Mapped[int | None] = mapped_column(default=None)
    duration_ms: Mapped[float | None] = mapped_column(default=None)
    applied_limit: Mapped[int | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(default=None)


class SecurityEventRecord(Base):
    """A blocked query or other security-relevant event."""

    __tablename__ = "security_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime]
    source: Mapped[str]
    event_type: Mapped[str]  # blocked_query | ...
    raw_sql: Mapped[str | None] = mapped_column(default=None)
    violations: Mapped[list[str]] = mapped_column(JSON, default=list)


class TableProfileRecord(Base):
    __tablename__ = "table_profiles"
    __table_args__ = (UniqueConstraint("schema_name", "table_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    schema_name: Mapped[str]
    table_name: Mapped[str]
    row_estimate: Mapped[int | None] = mapped_column(default=None)
    sampled_rows: Mapped[int] = mapped_column(default=0)
    column_count: Mapped[int] = mapped_column(default=0)


class ColumnProfileRecord(Base):
    __tablename__ = "column_profiles"
    __table_args__ = (UniqueConstraint("schema_name", "table_name", "column_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    schema_name: Mapped[str]
    table_name: Mapped[str]
    column_name: Mapped[str]
    null_fraction: Mapped[float] = mapped_column(default=0.0)
    distinct_estimate: Mapped[int] = mapped_column(default=0)
    duplicate_ratio: Mapped[float] = mapped_column(default=0.0)
    cardinality: Mapped[str] = mapped_column(default=CardinalityCategory.low.value)
    sampled_rows: Mapped[int] = mapped_column(default=0)
    min_value: Mapped[str | None] = mapped_column(default=None)
    max_value: Mapped[str | None] = mapped_column(default=None)
    avg_value: Mapped[float | None] = mapped_column(default=None)
    top_values: Mapped[list] = mapped_column(JSON, default=list)
    is_pii: Mapped[bool] = mapped_column(default=False)
    pii_type: Mapped[str | None] = mapped_column(default=None)
    pii_confidence: Mapped[float] = mapped_column(default=0.0)


class PiiClassificationRecord(Base):
    __tablename__ = "pii_classifications"
    __table_args__ = (UniqueConstraint("schema_name", "table_name", "column_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    schema_name: Mapped[str]
    table_name: Mapped[str]
    column_name: Mapped[str]
    pii_type: Mapped[str]
    confidence: Mapped[float] = mapped_column(default=0.0)
    method: Mapped[str] = mapped_column(default="name")


class SyncStateRecord(Base):
    __tablename__ = "sync_state"
    __table_args__ = (UniqueConstraint("table_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    table_name: Mapped[str]  # schema.table
    cursor_column: Mapped[str | None] = mapped_column(default=None)
    cursor_kind: Mapped[str | None] = mapped_column(default=None)
    last_cursor: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default="completed")
    row_count: Mapped[int] = mapped_column(default=0)
    mode: Mapped[str] = mapped_column(default="full")
    synced_at: Mapped[datetime | None] = mapped_column(default=None)


class SyncJobRecord(Base):
    __tablename__ = "sync_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    table_name: Mapped[str]
    mode: Mapped[str]
    rows: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(default="completed")
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime]
    error: Mapped[str | None] = mapped_column(default=None)


class ConversationRecord(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(primary_key=True)
    project: Mapped[str]
    title: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class ConversationMessageRecord(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str]
    content: Mapped[str]
    analysis_id: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime]


class ConversationContextSnapshotRecord(Base):
    __tablename__ = "conversation_context_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    analysis_id: Mapped[str | None] = mapped_column(default=None)
    context_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime]


class AnalysisResultRecord(Base):
    __tablename__ = "analysis_results"

    id: Mapped[str] = mapped_column(primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(default=None)
    question: Mapped[str] = mapped_column(default="")
    summary: Mapped[str | None] = mapped_column(default=None)
    structured_result_json: Mapped[str | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default="completed")
    created_at: Mapped[datetime | None] = mapped_column(default=None)


class SavedInvestigationRecord(Base):
    __tablename__ = "saved_investigations"
    __table_args__ = (UniqueConstraint("analysis_id"),)

    id: Mapped[str] = mapped_column(primary_key=True)
    project: Mapped[str]
    analysis_id: Mapped[str]
    conversation_id: Mapped[str | None] = mapped_column(default=None)
    title: Mapped[str]
    summary: Mapped[str] = mapped_column(default="")
    question: Mapped[str] = mapped_column(default="")
    result_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


# --------------------------------------------------------------------------------------------
# Read dataclasses (returned by the repository)
# --------------------------------------------------------------------------------------------


@dataclass
class TableSummary:
    schema: str
    name: str
    kind: str
    row_estimate: int | None
    size_bytes: int | None
    column_count: int
    category: str
    category_confidence: float

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}"


@dataclass
class ColumnInfo:
    name: str
    ordinal: int
    data_type: str
    nullable: bool
    is_primary_key: bool
    is_unique: bool
    comment: str | None


@dataclass
class IndexInfo:
    name: str
    columns: list[str]
    is_unique: bool
    is_primary: bool


@dataclass
class RelationshipInfo:
    source_schema: str
    source_table: str
    source_columns: list[str]
    target_schema: str
    target_table: str
    target_columns: list[str]
    kind: str
    confidence: float
    constraint_name: str | None

    @property
    def source_qualified(self) -> str:
        return f"{self.source_schema}.{self.source_table}"

    @property
    def target_qualified(self) -> str:
        return f"{self.target_schema}.{self.target_table}"


@dataclass
class TableDetail:
    summary: TableSummary
    columns: list[ColumnInfo]
    indexes: list[IndexInfo]
    outgoing: list[RelationshipInfo]  # this table references others
    incoming: list[RelationshipInfo]  # other tables reference this one


@dataclass
class ScanSummary:
    started_at: datetime
    finished_at: datetime
    server_version: str | None
    schema_count: int
    table_count: int
    view_count: int
    column_count: int
    relationship_count: int
