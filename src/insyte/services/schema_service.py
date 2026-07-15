"""Schema application service — shared by the TUI, MCP server, and Studio API.

Wraps the metadata repository and returns domain objects. Each caller formats them for its own
surface (JSON for MCP, tables for the TUI, response models for Studio).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from insyte.metadata.models import (
    ColumnProfile,
    RelationshipInfo,
    ScanSummary,
    TableDetail,
    TableSummary,
)
from insyte.metadata.repository import MetadataRepository


@dataclass
class DatabaseSummary:
    scanned: bool
    schemas: list[str] = field(default_factory=list)
    tables: list[TableSummary] = field(default_factory=list)
    last_scan: datetime | None = None


@dataclass
class SchemaMatch:
    table: str  # schema.table
    kind: str
    category: str
    matched_columns: list[str]


class SchemaService:
    """Read scanned schema metadata."""

    def __init__(self, metadata: MetadataRepository) -> None:
        self._metadata = metadata

    def has_metadata(self) -> bool:
        return self._metadata.has_metadata()

    def latest_scan(self) -> ScanSummary | None:
        return self._metadata.latest_scan()

    def list_schemas(self) -> list[str]:
        return self._metadata.list_schemas()

    def list_tables(self, schema: str | None = None) -> list[TableSummary]:
        return self._metadata.list_tables(schema)

    def get_table(self, schema: str | None, name: str) -> TableDetail | None:
        return self._metadata.get_table(schema, name)

    def list_relationships(self) -> list[RelationshipInfo]:
        return self._metadata.list_relationships()

    def column_profiles(self) -> list[ColumnProfile]:
        """Stored column profiles from ``insyte profile`` (empty if the project isn't profiled)."""

        if not self._metadata.has_profiles():
            return []
        return self._metadata.list_column_profiles()

    def database_summary(self) -> DatabaseSummary:
        if not self._metadata.has_metadata():
            return DatabaseSummary(scanned=False)
        latest = self._metadata.latest_scan()
        return DatabaseSummary(
            scanned=True,
            schemas=self._metadata.list_schemas(),
            tables=self._metadata.list_tables(),
            last_scan=latest.finished_at if latest else None,
        )

    def search(self, query: str, limit: int = 20) -> list[SchemaMatch]:
        ranked = self._metadata.search_documents(query, limit=limit * 3)
        if not ranked:
            return self._legacy_search(query, limit)
        tables = {summary.qualified_name: summary for summary in self._metadata.list_tables()}
        ordered: list[str] = []
        columns: dict[str, list[str]] = {}
        for item in ranked:
            payload = item.get("payload") or {}
            if isinstance(payload, str):
                payload = json.loads(payload)
            table = item["object_id"] if item["object_type"] == "table" else payload.get("table")
            if not table or table not in tables:
                continue
            if table not in ordered:
                ordered.append(table)
            column = payload.get("column")
            if column and column not in columns.setdefault(table, []):
                columns[table].append(column)
        return [
            SchemaMatch(
                table,
                tables[table].kind,
                tables[table].category,
                columns.get(table, []),
            )
            for table in ordered[:limit]
        ]

    def _legacy_search(self, query: str, limit: int) -> list[SchemaMatch]:
        """Compatibility path for projects that have not been rescanned since catalog support."""

        needle = query.lower().strip()
        matches: list[SchemaMatch] = []
        for summary in self._metadata.list_tables():
            detail = self._metadata.get_table(summary.schema, summary.name)
            if detail is None:
                continue
            matched = [
                column.name
                for column in detail.columns
                if needle in column.name.lower()
                or (column.comment and needle in column.comment.lower())
            ]
            if needle in summary.name.lower() or matched:
                matches.append(
                    SchemaMatch(summary.qualified_name, summary.kind, summary.category, matched)
                )
            if len(matches) >= limit:
                break
        return matches
