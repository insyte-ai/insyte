"""Schema scanner: read structure from a live PostgreSQL database.

The scanner runs entirely inside the connector's read-only transaction. It honours the
project's ``allowed_schemas`` and never records ``blocked_tables`` or ``blocked_columns`` — so
sensitive columns (password hashes, tokens) never enter local metadata or reach an AI client.
"""

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.engine.reflection import Inspector

from insyte.config.models import DatabaseSection
from insyte.connectors.base import DatabaseConnector
from insyte.logging_config import get_logger
from insyte.metadata.classifier import classify_table
from insyte.metadata.models import (
    Relationship,
    ScannedColumn,
    ScannedForeignKey,
    ScannedIndex,
    ScannedTable,
    ScanResult,
    TableKind,
)
from insyte.metadata.relationship_detector import detect_relationships

logger = get_logger("metadata.scanner")

_SYSTEM_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast"}

# Estimated rows and total size for every table in the target schemas, in one round-trip.
_TABLE_STATS_SQL = text(
    """
    SELECT n.nspname AS schema_name,
           c.relname AS table_name,
           c.reltuples::bigint AS row_estimate,
           pg_total_relation_size(c.oid) AS size_bytes
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'p', 'v', 'm')
      AND n.nspname = ANY(:schemas)
    """
)


def blocked_table_names(database: DatabaseSection) -> set[str]:
    """Return blocked tables normalised to lower-case (schema-qualified or bare)."""

    return {item.lower() for item in database.blocked_tables}


def blocked_column_names(database: DatabaseSection) -> set[str]:
    """Return blocked columns normalised to lower-case ``table.column`` keys.

    Both ``users.password_hash`` and ``public.users.password_hash`` are accepted in config; we
    key on the last two segments (``table.column``) for matching.
    """

    keys: set[str] = set()
    for item in database.blocked_columns:
        parts = item.lower().split(".")
        if len(parts) >= 2:
            keys.add(".".join(parts[-2:]))
    return keys


def is_table_blocked(schema: str, table: str, blocked: set[str]) -> bool:
    name = table.lower()
    qualified = f"{schema.lower()}.{name}"
    return name in blocked or qualified in blocked


def is_column_blocked(table: str, column: str, blocked: set[str]) -> bool:
    return f"{table.lower()}.{column.lower()}" in blocked


class SchemaScanner:
    """Scans a PostgreSQL database into an in-memory :class:`ScanResult`."""

    def __init__(self, connector: DatabaseConnector, database: DatabaseSection) -> None:
        self._connector = connector
        self._database = database

    def scan(self) -> ScanResult:
        """Run the scan inside a read-only transaction and return structured metadata."""

        blocked_tables = blocked_table_names(self._database)
        blocked_columns = blocked_column_names(self._database)
        target_schemas = self._resolve_schemas()

        logger.info("scan_started", extra={"schemas": target_schemas})

        with self._connector.read_only_transaction() as conn:
            inspector = inspect(conn)
            server_version = self._server_version(conn)
            stats = self._table_stats(conn, target_schemas)

            schemas: dict[str, str | None] = {}
            tables: list[ScannedTable] = []

            for schema in target_schemas:
                schemas[schema] = None
                for name, kind in self._iter_relations(inspector, schema):
                    if is_table_blocked(schema, name, blocked_tables):
                        continue
                    tables.append(
                        self._scan_table(inspector, schema, name, kind, stats, blocked_columns)
                    )

        relationships = detect_relationships(tables)
        self._classify(tables, relationships)

        logger.info(
            "scan_completed",
            extra={
                "schema_count": len(schemas),
                "table_count": len(tables),
                "relationship_count": len(relationships),
            },
        )
        return ScanResult(
            schemas=schemas,
            tables=tables,
            relationships=relationships,
            server_version=server_version,
        )

    def _resolve_schemas(self) -> list[str]:
        allowed = [s for s in self._database.allowed_schemas if s not in _SYSTEM_SCHEMAS]
        return allowed or ["public"]

    @staticmethod
    def _server_version(conn: Connection) -> str | None:
        row = conn.execute(text("SELECT version()")).scalar_one_or_none()
        return str(row) if row is not None else None

    @staticmethod
    def _table_stats(
        conn: Connection, schemas: list[str]
    ) -> dict[tuple[str, str], tuple[int | None, int | None]]:
        rows = conn.execute(_TABLE_STATS_SQL, {"schemas": schemas}).all()
        return {(r.schema_name, r.table_name): (r.row_estimate, r.size_bytes) for r in rows}

    @staticmethod
    def _iter_relations(inspector: Inspector, schema: str) -> list[tuple[str, TableKind]]:
        relations: list[tuple[str, TableKind]] = []
        for name in inspector.get_table_names(schema=schema):
            relations.append((name, TableKind.table))
        for name in inspector.get_view_names(schema=schema):
            relations.append((name, TableKind.view))
        return relations

    def _scan_table(
        self,
        inspector: Inspector,
        schema: str,
        name: str,
        kind: TableKind,
        stats: dict[tuple[str, str], tuple[int | None, int | None]],
        blocked_columns: set[str],
    ) -> ScannedTable:
        pk = inspector.get_pk_constraint(name, schema=schema)
        pk_columns = [c for c in (pk.get("constrained_columns") or []) if c]

        unique_columns = self._unique_columns(inspector, name, schema)

        columns: list[ScannedColumn] = []
        for ordinal, col in enumerate(inspector.get_columns(name, schema=schema)):
            if is_column_blocked(name, col["name"], blocked_columns):
                continue
            columns.append(
                ScannedColumn(
                    name=col["name"],
                    ordinal=ordinal,
                    data_type=str(col["type"]),
                    nullable=bool(col.get("nullable", True)),
                    default=_stringify(col.get("default")),
                    comment=col.get("comment"),
                    is_primary_key=col["name"] in pk_columns,
                    is_unique=col["name"] in unique_columns,
                )
            )

        foreign_keys = [
            ScannedForeignKey(
                name=fk.get("name"),
                columns=list(fk.get("constrained_columns") or []),
                target_schema=fk.get("referred_schema") or schema,
                target_table=fk.get("referred_table", ""),
                target_columns=list(fk.get("referred_columns") or []),
            )
            for fk in inspector.get_foreign_keys(name, schema=schema)
            if fk.get("referred_table")
        ]

        indexes = [
            ScannedIndex(
                name=idx.get("name") or "",
                columns=[c for c in (idx.get("column_names") or []) if c],
                is_unique=bool(idx.get("unique", False)),
                is_primary=False,
            )
            for idx in inspector.get_indexes(name, schema=schema)
        ]

        row_estimate, size_bytes = stats.get((schema, name), (None, None))
        comment = (inspector.get_table_comment(name, schema=schema) or {}).get("text")

        return ScannedTable(
            schema=schema,
            name=name,
            kind=kind,
            columns=columns,
            primary_key_columns=pk_columns,
            foreign_keys=foreign_keys,
            indexes=indexes,
            comment=comment,
            row_estimate=row_estimate,
            size_bytes=size_bytes,
        )

    @staticmethod
    def _unique_columns(inspector: Inspector, name: str, schema: str) -> set[str]:
        unique: set[str] = set()
        for constraint in inspector.get_unique_constraints(name, schema=schema):
            cols = [c for c in (constraint.get("column_names") or []) if c]
            if len(cols) == 1:
                unique.add(cols[0])
        for idx in inspector.get_indexes(name, schema=schema):
            idx_cols = [c for c in (idx.get("column_names") or []) if c]
            if idx.get("unique") and len(idx_cols) == 1:
                unique.add(idx_cols[0])
        return unique

    @staticmethod
    def _classify(tables: list[ScannedTable], relationships: list[Relationship]) -> None:
        for table in tables:
            outgoing = [
                r
                for r in relationships
                if r.source_schema == table.schema and r.source_table == table.name
            ]
            incoming = [
                r
                for r in relationships
                if r.target_schema == table.schema and r.target_table == table.name
            ]
            table.category, table.category_confidence = classify_table(table, outgoing, incoming)


def _stringify(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
