"""Persistence for scanned metadata in a per-project SQLite database.

Each project owns one ``metadata.sqlite`` file. Saving a scan *replaces* the structural
tables (schemas, tables, columns, indexes, relationships) in a single transaction while
leaving room for later milestones' tables (profiles, semantic layer, query history) to
coexist untouched.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, delete, select, text
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
    ConversationContextSnapshot,
    ConversationContextSnapshotRecord,
    ConversationMessage,
    ConversationMessageRecord,
    ConversationRecord,
    IndexInfo,
    IndexRecord,
    MetadataStateRecord,
    PiiClassificationRecord,
    ProfileResult,
    QueryHistoryRecord,
    RelationshipInfo,
    RelationshipRecord,
    SavedInvestigation,
    SavedInvestigationRecord,
    ScannedTable,
    ScanResult,
    ScanRun,
    ScanSummary,
    SchemaRecord,
    SearchDocumentRecord,
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
        self._backfill_metadata_state()
        self._ensure_search_index()

    def dispose(self) -> None:
        self._engine.dispose()

    def _backfill_metadata_state(self) -> None:
        """Adopt pre-fingerprint databases without discarding their valid profiles."""

        with Session(self._engine) as session, session.begin():
            if session.get(MetadataStateRecord, "schema_fingerprint") is not None:
                return
            tables = list(
                session.execute(
                    select(TableRecord).order_by(TableRecord.schema_name, TableRecord.name)
                ).scalars()
            )
            if not tables:
                return
            payload = []
            for table in tables:
                columns = list(
                    session.execute(
                        select(ColumnRecord)
                        .where(ColumnRecord.table_id == table.id)
                        .order_by(ColumnRecord.ordinal)
                    ).scalars()
                )
                payload.append(
                    {
                        "schema": table.schema_name,
                        "name": table.name,
                        "kind": table.kind,
                        "columns": [
                            {
                                "name": column.name,
                                "type": column.data_type,
                                "nullable": column.nullable,
                                "primary": column.is_primary_key,
                                "unique": column.is_unique,
                            }
                            for column in columns
                        ],
                    }
                )
            fingerprint = _fingerprint_payload(payload)
            session.add(MetadataStateRecord(key="schema_fingerprint", value=fingerprint))
            if session.execute(select(ColumnProfileRecord.id).limit(1)).first() is not None:
                session.add(
                    MetadataStateRecord(key="profile_schema_fingerprint", value=fingerprint)
                )

    # -- writing -----------------------------------------------------------------------------

    def save_scan(
        self, result: ScanResult, *, started_at: datetime, finished_at: datetime
    ) -> ScanSummary:
        """Replace structural metadata with a fresh scan and record the run."""

        fingerprint = _scan_fingerprint(result)
        with Session(self._engine) as session, session.begin():
            previous = session.get(MetadataStateRecord, "schema_fingerprint")
            if previous is not None and previous.value != fingerprint:
                session.execute(delete(PiiClassificationRecord))
                session.execute(delete(ColumnProfileRecord))
                session.execute(delete(TableProfileRecord))
            for record in _STRUCTURAL_RECORDS:
                session.execute(delete(record))

            schema_ids = self._insert_schemas(session, result)
            for table in result.tables:
                self._insert_table(session, table, schema_ids[table.schema])
            self._insert_relationships(session, result)
            self._replace_search_documents(session, result)
            session.merge(MetadataStateRecord(key="schema_fingerprint", value=fingerprint))

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

    def _ensure_search_index(self) -> None:
        """Create the optional FTS5 index; LIKE search remains the portability fallback."""

        try:
            with self._engine.begin() as conn:
                conn.exec_driver_sql(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS search_documents_fts "
                    "USING fts5(title, content, content='search_documents', content_rowid='id')"
                )
        except Exception:  # noqa: BLE001 - some SQLite builds omit FTS5
            logger.info("metadata_fts_unavailable")

    @staticmethod
    def _replace_search_documents(session: Session, result: ScanResult) -> None:
        session.execute(delete(SearchDocumentRecord))
        for table in result.tables:
            relationship_terms: list[str] = []
            for rel in result.relationships:
                if rel.source_schema == table.schema and rel.source_table == table.name:
                    relationship_terms.append(f"joins {rel.target_schema}.{rel.target_table}")
                if rel.target_schema == table.schema and rel.target_table == table.name:
                    relationship_terms.append(f"joined from {rel.source_schema}.{rel.source_table}")
            qualified = table.qualified_name
            session.add(
                SearchDocumentRecord(
                    object_type="table",
                    object_id=qualified,
                    title=f"{qualified} {table.name.replace('_', ' ')}",
                    content=" ".join(
                        filter(
                            None,
                            [
                                table.comment,
                                table.category.value,
                                *relationship_terms,
                                *(column.name.replace("_", " ") for column in table.columns),
                            ],
                        )
                    ),
                    payload={"kind": table.kind.value, "category": table.category.value},
                )
            )
            for column in table.columns:
                session.add(
                    SearchDocumentRecord(
                        object_type="column",
                        object_id=f"{qualified}.{column.name}",
                        title=f"{column.name} {column.name.replace('_', ' ')} {qualified}",
                        content=" ".join(filter(None, [column.comment, column.data_type])),
                        payload={"table": qualified, "column": column.name},
                    )
                )
        session.flush()
        try:
            session.execute(
                text("INSERT INTO search_documents_fts(search_documents_fts) VALUES('rebuild')")
            )
        except Exception:  # noqa: BLE001 - FTS is optional
            logger.info("metadata_fts_rebuild_skipped")

    def search_documents(self, query: str, limit: int = 20) -> list[dict]:
        """Rank scanned metadata with FTS5, falling back to portable LIKE matching."""

        terms = re.findall(r"[a-z0-9_]+", query.lower())
        if not terms:
            return []
        fts_query = " OR ".join(f'"{term}"' for term in terms)
        sql = text(
            "SELECT d.object_type, d.object_id, d.title, d.payload "
            "FROM search_documents_fts f "
            "JOIN search_documents d ON d.id = f.rowid "
            "WHERE search_documents_fts MATCH :query ORDER BY bm25(search_documents_fts) "
            "LIMIT :limit"
        )
        with Session(self._engine) as session:
            try:
                rows = session.execute(sql, {"query": fts_query, "limit": limit}).mappings()
                found = [dict(row) for row in rows]
                if found:
                    return found
            except Exception:  # noqa: BLE001 - FTS is an optional optimization
                session.rollback()
            pattern = f"%{'%'.join(terms)}%"
            records = session.execute(
                select(SearchDocumentRecord)
                .where(
                    (SearchDocumentRecord.title.ilike(pattern))
                    | (SearchDocumentRecord.content.ilike(pattern))
                )
                .limit(limit)
            ).scalars()
            return [
                {
                    "object_type": record.object_type,
                    "object_id": record.object_id,
                    "title": record.title,
                    "payload": record.payload,
                }
                for record in records
            ]

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

    def list_table_details(self, limit: int | None = None) -> list[TableDetail]:
        """Load all table details in four bounded queries instead of one query per table."""

        with Session(self._engine) as session:
            table_stmt = select(TableRecord).order_by(TableRecord.schema_name, TableRecord.name)
            if limit is not None:
                table_stmt = table_stmt.limit(limit)
            tables = list(session.execute(table_stmt).scalars())
            if not tables:
                return []
            ids = [table.id for table in tables]
            columns = list(
                session.execute(
                    select(ColumnRecord)
                    .where(ColumnRecord.table_id.in_(ids))
                    .order_by(ColumnRecord.table_id, ColumnRecord.ordinal)
                ).scalars()
            )
            indexes = list(
                session.execute(select(IndexRecord).where(IndexRecord.table_id.in_(ids))).scalars()
            )
            relationships = list(session.execute(select(RelationshipRecord)).scalars())

        columns_by_table: dict[int, list[ColumnInfo]] = {}
        for column in columns:
            columns_by_table.setdefault(column.table_id, []).append(_column_info(column))
        indexes_by_table: dict[int, list[IndexInfo]] = {}
        for index in indexes:
            indexes_by_table.setdefault(index.table_id, []).append(_index_info(index))
        return [
            TableDetail(
                summary=_table_summary(table),
                columns=columns_by_table.get(table.id, []),
                indexes=indexes_by_table.get(table.id, []),
                outgoing=[
                    _relationship_info(relationship)
                    for relationship in relationships
                    if relationship.source_schema == table.schema_name
                    and relationship.source_table == table.name
                ],
                incoming=[
                    _relationship_info(relationship)
                    for relationship in relationships
                    if relationship.target_schema == table.schema_name
                    and relationship.target_table == table.name
                ],
            )
            for table in tables
        ]

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
            current = session.get(MetadataStateRecord, "schema_fingerprint")
            if current is not None:
                session.merge(
                    MetadataStateRecord(key="profile_schema_fingerprint", value=current.value)
                )

    def has_profiles(self) -> bool:
        with Session(self._engine) as session:
            schema = session.get(MetadataStateRecord, "schema_fingerprint")
            profile = session.get(MetadataStateRecord, "profile_schema_fingerprint")
            return (
                schema is not None
                and profile is not None
                and schema.value == profile.value
                and session.execute(select(ColumnProfileRecord.id).limit(1)).first() is not None
            )

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
            session.execute(
                delete(ConversationContextSnapshotRecord).where(
                    ConversationContextSnapshotRecord.conversation_id == conversation_id
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

    def save_context_snapshot(
        self, conversation_id: str, analysis_id: str | None, context_json: dict
    ) -> ConversationContextSnapshot:
        now = utcnow()
        with Session(self._engine) as session, session.begin():
            record = ConversationContextSnapshotRecord(
                conversation_id=conversation_id,
                analysis_id=analysis_id,
                context_json=context_json,
                created_at=now,
            )
            session.add(record)
            session.flush()
            return _context_snapshot(record)

    def latest_context_snapshot(self, conversation_id: str) -> ConversationContextSnapshot | None:
        with Session(self._engine) as session:
            record = (
                session.execute(
                    select(ConversationContextSnapshotRecord)
                    .where(ConversationContextSnapshotRecord.conversation_id == conversation_id)
                    .order_by(ConversationContextSnapshotRecord.id.desc())
                )
                .scalars()
                .first()
            )
            return _context_snapshot(record) if record is not None else None

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

    # -- saved investigations (Studio) ------------------------------------------------------

    def save_investigation(
        self,
        investigation_id: str,
        project: str,
        analysis_id: str,
        title: str,
        summary: str,
        question: str,
        result_json: dict,
        conversation_id: str | None = None,
    ) -> SavedInvestigation:
        now = utcnow()
        with Session(self._engine) as session, session.begin():
            record = session.execute(
                select(SavedInvestigationRecord).where(
                    SavedInvestigationRecord.analysis_id == analysis_id
                )
            ).scalar_one_or_none()
            if record is None:
                record = SavedInvestigationRecord(
                    id=investigation_id,
                    project=project,
                    analysis_id=analysis_id,
                    conversation_id=conversation_id,
                    title=title,
                    summary=summary,
                    question=question,
                    result_json=result_json,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                record.project = project
                record.conversation_id = conversation_id
                record.title = title
                record.summary = summary
                record.question = question
                record.result_json = result_json
                record.updated_at = now
            session.flush()
            return _saved_investigation(record)

    def list_investigations(self, project: str) -> list[SavedInvestigation]:
        with Session(self._engine) as session:
            records = session.execute(
                select(SavedInvestigationRecord)
                .where(SavedInvestigationRecord.project == project)
                .order_by(SavedInvestigationRecord.updated_at.desc())
            ).scalars()
            return [_saved_investigation(r) for r in records]

    def get_investigation(self, investigation_id: str) -> SavedInvestigation | None:
        with Session(self._engine) as session:
            record = session.get(SavedInvestigationRecord, investigation_id)
            return _saved_investigation(record) if record is not None else None

    def set_investigation_title(self, investigation_id: str, title: str) -> bool:
        with Session(self._engine) as session, session.begin():
            record = session.get(SavedInvestigationRecord, investigation_id)
            if record is None:
                return False
            record.title = title
            record.updated_at = utcnow()
            return True

    def delete_investigation(self, investigation_id: str) -> bool:
        with Session(self._engine) as session, session.begin():
            record = session.get(SavedInvestigationRecord, investigation_id)
            if record is None:
                return False
            session.delete(record)
            return True

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


def _context_snapshot(record: ConversationContextSnapshotRecord) -> ConversationContextSnapshot:
    return ConversationContextSnapshot(
        id=record.id,
        conversation_id=record.conversation_id,
        analysis_id=record.analysis_id,
        context_json=dict(record.context_json),
        created_at=record.created_at,
    )


def _saved_investigation(record: SavedInvestigationRecord) -> SavedInvestigation:
    return SavedInvestigation(
        id=record.id,
        project=record.project,
        analysis_id=record.analysis_id,
        conversation_id=record.conversation_id,
        title=record.title,
        summary=record.summary,
        question=record.question,
        result_json=dict(record.result_json),
        created_at=record.created_at,
        updated_at=record.updated_at,
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


def _scan_fingerprint(result: ScanResult) -> str:
    """Hash only schema facts that affect profiles and semantic definitions."""

    payload = [
        {
            "schema": table.schema,
            "name": table.name,
            "kind": table.kind.value,
            "columns": [
                {
                    "name": column.name,
                    "type": column.data_type,
                    "nullable": column.nullable,
                    "primary": column.is_primary_key,
                    "unique": column.is_unique,
                }
                for column in table.columns
            ],
        }
        for table in sorted(result.tables, key=lambda item: item.qualified_name)
    ]
    return _fingerprint_payload(payload)


def _fingerprint_payload(payload: list[dict]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


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
