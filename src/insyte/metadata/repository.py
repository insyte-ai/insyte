"""Persistence for scanned metadata in a per-project SQLite database.

Each project owns one ``metadata.sqlite`` file. Saving a scan *replaces* the structural
tables (schemas, tables, columns, indexes, relationships) in a single transaction while
leaving room for later milestones' tables (profiles, semantic layer, query history) to
coexist untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session

from insyte.logging_config import get_logger
from insyte.metadata.models import (
    AnalysisResultRecord,
    Base,
    CardinalityCategory,
    ColumnInfo,
    ColumnProfile,
    ColumnProfileRecord,
    ColumnRecord,
    Conversation,
    ConversationMessage,
    ConversationMessageRecord,
    ConversationRecord,
    IndexInfo,
    IndexRecord,
    PiiClassificationRecord,
    ProfileResult,
    QueryHistoryRecord,
    RelationshipInfo,
    RelationshipRecord,
    ScannedTable,
    ScanResult,
    ScanRun,
    ScanSummary,
    SchemaRecord,
    SecurityEventRecord,
    SyncJobRecord,
    SyncState,
    SyncStateRecord,
    TableCategory,
    TableDetail,
    TableKind,
    TableProfileRecord,
    TableRecord,
    TableSummary,
)
from insyte.query.models import (
    QueryHistoryEntry,
    SecurityEventEntry,
)

logger = get_logger("metadata.repository")

# Structural tables replaced on each scan, in FK-safe delete order.
_STRUCTURAL_RECORDS = (RelationshipRecord, IndexRecord, ColumnRecord, TableRecord, SchemaRecord)


class MetadataRepository:
    """Reads and writes scanned metadata for a single project."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(self._engine)

    def dispose(self) -> None:
        self._engine.dispose()

    # -- writing -----------------------------------------------------------------------------

    def save_scan(
        self, result: ScanResult, *, started_at: datetime, finished_at: datetime
    ) -> ScanSummary:
        """Replace structural metadata with a fresh scan and record the run."""

        with Session(self._engine) as session, session.begin():
            for record in _STRUCTURAL_RECORDS:
                session.execute(delete(record))

            schema_ids = self._insert_schemas(session, result)
            for table in result.tables:
                self._insert_table(session, table, schema_ids[table.schema])
            self._insert_relationships(session, result)

            view_count = sum(1 for t in result.tables if t.kind is TableKind.view)
            column_count = sum(len(t.columns) for t in result.tables)
            run = ScanRun(
                started_at=started_at,
                finished_at=finished_at,
                server_version=result.server_version,
                schema_count=len(result.schemas),
                table_count=len(result.tables) - view_count,
                view_count=view_count,
                column_count=column_count,
                relationship_count=len(result.relationships),
            )
            session.add(run)
            session.flush()
            summary = _run_to_summary(run)

        logger.info(
            "scan_persisted",
            extra={"tables": summary.table_count, "relationships": summary.relationship_count},
        )
        return summary

    @staticmethod
    def _insert_schemas(session: Session, result: ScanResult) -> dict[str, int]:
        ids: dict[str, int] = {}
        for name, comment in result.schemas.items():
            record = SchemaRecord(name=name, comment=comment)
            session.add(record)
            session.flush()
            ids[name] = record.id
        return ids

    @staticmethod
    def _insert_table(session: Session, table: ScannedTable, schema_id: int) -> None:
        record = TableRecord(
            schema_id=schema_id,
            schema_name=table.schema,
            name=table.name,
            kind=table.kind.value,
            comment=table.comment,
            row_estimate=table.row_estimate,
            size_bytes=table.size_bytes,
            column_count=len(table.columns),
            category=table.category.value,
            category_confidence=table.category_confidence,
        )
        session.add(record)
        session.flush()

        session.add_all(
            ColumnRecord(
                table_id=record.id,
                schema_name=table.schema,
                table_name=table.name,
                name=col.name,
                ordinal=col.ordinal,
                data_type=col.data_type,
                nullable=col.nullable,
                default=col.default,
                is_primary_key=col.is_primary_key,
                is_unique=col.is_unique,
                comment=col.comment,
            )
            for col in table.columns
        )
        session.add_all(
            IndexRecord(
                table_id=record.id,
                schema_name=table.schema,
                table_name=table.name,
                name=idx.name,
                columns=idx.columns,
                is_unique=idx.is_unique,
                is_primary=idx.is_primary,
            )
            for idx in table.indexes
        )

    @staticmethod
    def _insert_relationships(session: Session, result: ScanResult) -> None:
        session.add_all(
            RelationshipRecord(
                source_schema=rel.source_schema,
                source_table=rel.source_table,
                source_columns=rel.source_columns,
                target_schema=rel.target_schema,
                target_table=rel.target_table,
                target_columns=rel.target_columns,
                kind=rel.kind.value,
                confidence=rel.confidence,
                constraint_name=rel.constraint_name,
            )
            for rel in result.relationships
        )

    # -- reading -----------------------------------------------------------------------------

    def has_metadata(self) -> bool:
        with Session(self._engine) as session:
            return session.execute(select(TableRecord.id).limit(1)).first() is not None

    def latest_scan(self) -> ScanSummary | None:
        with Session(self._engine) as session:
            run = session.execute(
                select(ScanRun).order_by(ScanRun.finished_at.desc()).limit(1)
            ).scalar_one_or_none()
            return _run_to_summary(run) if run is not None else None

    def list_schemas(self) -> list[str]:
        with Session(self._engine) as session:
            return list(
                session.execute(select(SchemaRecord.name).order_by(SchemaRecord.name)).scalars()
            )

    def list_tables(self, schema: str | None = None) -> list[TableSummary]:
        stmt = select(TableRecord).order_by(TableRecord.schema_name, TableRecord.name)
        if schema is not None:
            stmt = stmt.where(TableRecord.schema_name == schema)
        with Session(self._engine) as session:
            return [_table_summary(r) for r in session.execute(stmt).scalars()]

    def get_table(self, schema: str | None, name: str) -> TableDetail | None:
        with Session(self._engine) as session:
            record = self._find_table(session, schema, name)
            if record is None:
                return None
            columns = session.execute(
                select(ColumnRecord)
                .where(ColumnRecord.table_id == record.id)
                .order_by(ColumnRecord.ordinal)
            ).scalars()
            indexes = session.execute(
                select(IndexRecord).where(IndexRecord.table_id == record.id)
            ).scalars()
            outgoing = session.execute(
                select(RelationshipRecord).where(
                    RelationshipRecord.source_schema == record.schema_name,
                    RelationshipRecord.source_table == record.name,
                )
            ).scalars()
            incoming = session.execute(
                select(RelationshipRecord).where(
                    RelationshipRecord.target_schema == record.schema_name,
                    RelationshipRecord.target_table == record.name,
                )
            ).scalars()
            return TableDetail(
                summary=_table_summary(record),
                columns=[_column_info(c) for c in columns],
                indexes=[_index_info(i) for i in indexes],
                outgoing=[_relationship_info(r) for r in outgoing],
                incoming=[_relationship_info(r) for r in incoming],
            )

    def list_relationships(self) -> list[RelationshipInfo]:
        with Session(self._engine) as session:
            records = session.execute(
                select(RelationshipRecord).order_by(
                    RelationshipRecord.source_schema, RelationshipRecord.source_table
                )
            ).scalars()
            return [_relationship_info(r) for r in records]

    # -- audit (AuditRecorder protocol) ------------------------------------------------------

    def record_query(self, entry: QueryHistoryEntry) -> None:
        with Session(self._engine) as session, session.begin():
            session.add(
                QueryHistoryRecord(
                    created_at=entry.created_at or utcnow(),
                    source=entry.source,
                    raw_sql=entry.raw_sql,
                    normalized_sql=entry.normalized_sql,
                    referenced_tables=entry.referenced_tables,
                    status=entry.status,
                    row_count=entry.row_count,
                    duration_ms=entry.duration_ms,
                    applied_limit=entry.applied_limit,
                    error=entry.error,
                )
            )

    def record_security_event(self, event: SecurityEventEntry) -> None:
        with Session(self._engine) as session, session.begin():
            session.add(
                SecurityEventRecord(
                    created_at=event.created_at or utcnow(),
                    source=event.source,
                    event_type=event.event_type,
                    raw_sql=event.raw_sql,
                    violations=event.violations,
                )
            )

    def list_query_history(self, limit: int = 20) -> list[QueryHistoryEntry]:
        with Session(self._engine) as session:
            records = session.execute(
                select(QueryHistoryRecord).order_by(QueryHistoryRecord.id.desc()).limit(limit)
            ).scalars()
            return [_query_history_entry(r) for r in records]

    def list_security_events(self, limit: int = 20) -> list[SecurityEventEntry]:
        with Session(self._engine) as session:
            records = session.execute(
                select(SecurityEventRecord).order_by(SecurityEventRecord.id.desc()).limit(limit)
            ).scalars()
            return [_security_event_entry(r) for r in records]

    # -- profiles ----------------------------------------------------------------------------

    def save_profiles(self, result: ProfileResult) -> None:
        """Replace stored profiles with a fresh profiling run."""

        with Session(self._engine) as session, session.begin():
            session.execute(delete(PiiClassificationRecord))
            session.execute(delete(ColumnProfileRecord))
            session.execute(delete(TableProfileRecord))
            session.add_all(
                TableProfileRecord(
                    schema_name=tp.schema,
                    table_name=tp.table,
                    row_estimate=tp.row_estimate,
                    sampled_rows=tp.sampled_rows,
                    column_count=tp.column_count,
                )
                for tp in result.table_profiles
            )
            for cp in result.column_profiles:
                session.add(
                    ColumnProfileRecord(
                        schema_name=cp.schema,
                        table_name=cp.table,
                        column_name=cp.column,
                        null_fraction=cp.null_fraction,
                        distinct_estimate=cp.distinct_estimate,
                        duplicate_ratio=cp.duplicate_ratio,
                        cardinality=cp.cardinality.value,
                        sampled_rows=cp.sampled_rows,
                        min_value=cp.min_value,
                        max_value=cp.max_value,
                        avg_value=cp.avg_value,
                        top_values=[list(item) for item in cp.top_values],
                        is_pii=cp.is_pii,
                        pii_type=cp.pii_type,
                        pii_confidence=cp.pii_confidence,
                    )
                )
                if cp.is_pii and cp.pii_type:
                    session.add(
                        PiiClassificationRecord(
                            schema_name=cp.schema,
                            table_name=cp.table,
                            column_name=cp.column,
                            pii_type=cp.pii_type,
                            confidence=cp.pii_confidence,
                            method="profile",
                        )
                    )

    def has_profiles(self) -> bool:
        with Session(self._engine) as session:
            return session.execute(select(ColumnProfileRecord.id).limit(1)).first() is not None

    def list_column_profiles(self, table: str | None = None) -> list[ColumnProfile]:
        stmt = select(ColumnProfileRecord).order_by(
            ColumnProfileRecord.schema_name,
            ColumnProfileRecord.table_name,
            ColumnProfileRecord.id,
        )
        if table is not None:
            stmt = stmt.where(ColumnProfileRecord.table_name == table)
        with Session(self._engine) as session:
            return [_column_profile(r) for r in session.execute(stmt).scalars()]

    # -- sync state --------------------------------------------------------------------------

    def get_sync_state(self, table: str) -> SyncState | None:
        with Session(self._engine) as session:
            record = session.execute(
                select(SyncStateRecord).where(SyncStateRecord.table_name == table)
            ).scalar_one_or_none()
            return _sync_state(record) if record is not None else None

    def list_sync_states(self) -> list[SyncState]:
        with Session(self._engine) as session:
            records = session.execute(
                select(SyncStateRecord).order_by(SyncStateRecord.table_name)
            ).scalars()
            return [_sync_state(r) for r in records]

    def upsert_sync_state(self, state: SyncState) -> None:
        with Session(self._engine) as session, session.begin():
            record = session.execute(
                select(SyncStateRecord).where(SyncStateRecord.table_name == state.table)
            ).scalar_one_or_none()
            if record is None:
                record = SyncStateRecord(table_name=state.table)
                session.add(record)
            record.cursor_column = state.cursor_column
            record.cursor_kind = state.cursor_kind
            record.last_cursor = state.last_cursor
            record.status = state.status
            record.row_count = state.row_count
            record.mode = state.mode
            record.synced_at = utcnow()

    def record_sync_job(
        self,
        table: str,
        mode: str,
        rows: int,
        status: str,
        started_at: datetime,
        finished_at: datetime,
        error: str | None = None,
    ) -> None:
        with Session(self._engine) as session, session.begin():
            session.add(
                SyncJobRecord(
                    table_name=table,
                    mode=mode,
                    rows=rows,
                    status=status,
                    started_at=started_at,
                    finished_at=finished_at,
                    error=error,
                )
            )

    # -- conversations (Studio) --------------------------------------------------------------

    def create_conversation(self, conversation_id: str, project: str, title: str) -> Conversation:
        now = utcnow()
        with Session(self._engine) as session, session.begin():
            session.add(
                ConversationRecord(
                    id=conversation_id, project=project, title=title, created_at=now, updated_at=now
                )
            )
        return Conversation(conversation_id, project, title, now, now)

    def list_conversations(self, project: str) -> list[Conversation]:
        with Session(self._engine) as session:
            records = session.execute(
                select(ConversationRecord)
                .where(ConversationRecord.project == project)
                .order_by(ConversationRecord.updated_at.desc())
            ).scalars()
            return [_conversation(r) for r in records]

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        with Session(self._engine) as session:
            record = session.get(ConversationRecord, conversation_id)
            return _conversation(record) if record is not None else None

    def set_conversation_title(self, conversation_id: str, title: str) -> None:
        with Session(self._engine) as session, session.begin():
            record = session.get(ConversationRecord, conversation_id)
            if record is not None:
                record.title = title

    def delete_conversation(self, conversation_id: str) -> bool:
        with Session(self._engine) as session, session.begin():
            record = session.get(ConversationRecord, conversation_id)
            if record is None:
                return False
            session.execute(
                delete(ConversationMessageRecord).where(
                    ConversationMessageRecord.conversation_id == conversation_id
                )
            )
            session.delete(record)
            return True

    def add_message(
        self, conversation_id: str, role: str, content: str, analysis_id: str | None = None
    ) -> ConversationMessage:
        now = utcnow()
        with Session(self._engine) as session, session.begin():
            record = ConversationMessageRecord(
                conversation_id=conversation_id,
                role=role,
                content=content,
                analysis_id=analysis_id,
                created_at=now,
            )
            session.add(record)
            conversation = session.get(ConversationRecord, conversation_id)
            if conversation is not None:
                conversation.updated_at = now
            session.flush()
            return _message(record)

    def list_messages(self, conversation_id: str) -> list[ConversationMessage]:
        with Session(self._engine) as session:
            records = session.execute(
                select(ConversationMessageRecord)
                .where(ConversationMessageRecord.conversation_id == conversation_id)
                .order_by(ConversationMessageRecord.id)
            ).scalars()
            return [_message(r) for r in records]

    def save_analysis_result(
        self,
        analysis_id: str,
        question: str,
        summary: str | None,
        structured_result_json: str | None,
        conversation_id: str | None = None,
        status: str = "completed",
    ) -> None:
        with Session(self._engine) as session, session.begin():
            record = session.get(AnalysisResultRecord, analysis_id)
            if record is None:
                record = AnalysisResultRecord(id=analysis_id, created_at=utcnow())
                session.add(record)
            record.conversation_id = conversation_id
            record.question = question
            record.summary = summary
            record.structured_result_json = structured_result_json
            record.status = status

    def get_analysis_result(self, analysis_id: str) -> str | None:
        with Session(self._engine) as session:
            record = session.get(AnalysisResultRecord, analysis_id)
            return record.structured_result_json if record is not None else None

    def get_analysis_request(self, analysis_id: str) -> tuple[str, str | None] | None:
        """Return ``(question, conversation_id)`` for a stored analysis, or None."""

        with Session(self._engine) as session:
            record = session.get(AnalysisResultRecord, analysis_id)
            if record is None:
                return None
            return record.question, record.conversation_id

    @staticmethod
    def _find_table(session: Session, schema: str | None, name: str) -> TableRecord | None:
        stmt = select(TableRecord).where(TableRecord.name == name)
        if schema is not None:
            stmt = stmt.where(TableRecord.schema_name == schema)
        return session.execute(stmt.order_by(TableRecord.schema_name)).scalars().first()


def _table_summary(record: TableRecord) -> TableSummary:
    return TableSummary(
        schema=record.schema_name,
        name=record.name,
        kind=record.kind,
        row_estimate=record.row_estimate,
        size_bytes=record.size_bytes,
        column_count=record.column_count,
        category=record.category,
        category_confidence=record.category_confidence,
    )


def _column_info(record: ColumnRecord) -> ColumnInfo:
    return ColumnInfo(
        name=record.name,
        ordinal=record.ordinal,
        data_type=record.data_type,
        nullable=record.nullable,
        is_primary_key=record.is_primary_key,
        is_unique=record.is_unique,
        comment=record.comment,
    )


def _index_info(record: IndexRecord) -> IndexInfo:
    return IndexInfo(
        name=record.name,
        columns=list(record.columns),
        is_unique=record.is_unique,
        is_primary=record.is_primary,
    )


def _relationship_info(record: RelationshipRecord) -> RelationshipInfo:
    return RelationshipInfo(
        source_schema=record.source_schema,
        source_table=record.source_table,
        source_columns=list(record.source_columns),
        target_schema=record.target_schema,
        target_table=record.target_table,
        target_columns=list(record.target_columns),
        kind=record.kind,
        confidence=record.confidence,
        constraint_name=record.constraint_name,
    )


def _conversation(record: ConversationRecord) -> Conversation:
    return Conversation(
        id=record.id,
        project=record.project,
        title=record.title,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _message(record: ConversationMessageRecord) -> ConversationMessage:
    return ConversationMessage(
        id=record.id,
        conversation_id=record.conversation_id,
        role=record.role,
        content=record.content,
        analysis_id=record.analysis_id,
        created_at=record.created_at,
    )


def _sync_state(record: SyncStateRecord) -> SyncState:
    return SyncState(
        table=record.table_name,
        cursor_column=record.cursor_column,
        cursor_kind=record.cursor_kind,
        last_cursor=record.last_cursor,
        status=record.status,
        row_count=record.row_count,
        mode=record.mode,
    )


def _column_profile(record: ColumnProfileRecord) -> ColumnProfile:
    return ColumnProfile(
        schema=record.schema_name,
        table=record.table_name,
        column=record.column_name,
        null_fraction=record.null_fraction,
        distinct_estimate=record.distinct_estimate,
        duplicate_ratio=record.duplicate_ratio,
        cardinality=CardinalityCategory(record.cardinality),
        sampled_rows=record.sampled_rows,
        min_value=record.min_value,
        max_value=record.max_value,
        avg_value=record.avg_value,
        top_values=[(item[0], item[1]) for item in record.top_values],
        is_pii=record.is_pii,
        pii_type=record.pii_type,
        pii_confidence=record.pii_confidence,
    )


def _query_history_entry(record: QueryHistoryRecord) -> QueryHistoryEntry:
    return QueryHistoryEntry(
        source=record.source,
        raw_sql=record.raw_sql,
        normalized_sql=record.normalized_sql,
        referenced_tables=list(record.referenced_tables),
        status=record.status,
        row_count=record.row_count,
        duration_ms=record.duration_ms,
        applied_limit=record.applied_limit,
        error=record.error,
        created_at=record.created_at,
    )


def _security_event_entry(record: SecurityEventRecord) -> SecurityEventEntry:
    return SecurityEventEntry(
        source=record.source,
        event_type=record.event_type,
        raw_sql=record.raw_sql,
        violations=list(record.violations),
        created_at=record.created_at,
    )


def _run_to_summary(run: ScanRun) -> ScanSummary:
    return ScanSummary(
        started_at=run.started_at,
        finished_at=run.finished_at,
        server_version=run.server_version,
        schema_count=run.schema_count,
        table_count=run.table_count,
        view_count=run.view_count,
        column_count=run.column_count,
        relationship_count=run.relationship_count,
    )


def utcnow() -> datetime:
    """Timezone-aware current time (kept here so scan timestamps have one source)."""

    return datetime.now(UTC)


# Re-exported so callers need only import from the repository module.
__all__ = ["MetadataRepository", "TableCategory", "utcnow"]
