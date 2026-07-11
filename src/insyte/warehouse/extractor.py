"""Extract a table (or a delta) from PostgreSQL into a Parquet file.

Extraction runs inside the connector's read-only, timeout-bounded transaction. Rows are read
into an Arrow table and written to Parquet in the project's cache directory, which DuckDB then
loads. Incremental extraction filters on a cursor column and reports the new maximum cursor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import text

from insyte.connectors.base import DatabaseConnector
from insyte.logging_config import get_logger

logger = get_logger("warehouse.extractor")


@dataclass
class Extraction:
    table: str  # schema.table
    parquet_path: Path
    row_count: int
    max_cursor: str | None


class Extractor:
    """Reads a table from PostgreSQL and writes it to Parquet."""

    def __init__(self, connector: DatabaseConnector, cache_dir: Path) -> None:
        self._connector = connector
        self._cache_dir = cache_dir

    def extract(
        self,
        schema: str,
        table: str,
        columns: list[str],
        *,
        cursor_column: str | None = None,
        cursor_kind: str | None = None,
        last_cursor: str | None = None,
    ) -> Extraction:
        """Extract a full table, or the rows after ``last_cursor`` when given."""

        select = ", ".join(_quote(c) for c in columns)
        sql = f"SELECT {select} FROM {_quote(schema)}.{_quote(table)}"
        params: dict[str, object] = {}
        if cursor_column and last_cursor is not None:
            sql += f" WHERE {_quote(cursor_column)} > :last_cursor"
            params["last_cursor"] = _coerce_cursor(last_cursor, cursor_kind)
        if cursor_column:
            sql += f" ORDER BY {_quote(cursor_column)}"

        with self._connector.read_only_transaction() as conn:
            result = conn.execute(text(sql), params)
            column_names = list(result.keys())
            rows = list(result.fetchall())

        arrow = _rows_to_arrow(column_names, rows)
        suffix = "delta" if last_cursor is not None else "full"
        path = self._cache_dir / f"{schema}.{table}.{suffix}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(arrow, path)

        max_cursor = _max_cursor(column_names, rows, cursor_column)
        logger.info(
            "extracted",
            extra={
                "table": f"{schema}.{table}",
                "rows": len(rows),
                "incremental": last_cursor is not None,
            },
        )
        return Extraction(f"{schema}.{table}", path, len(rows), max_cursor)


def _coerce_cursor(last_cursor: str, cursor_kind: str | None) -> object:
    """Convert the stored (string) cursor to a typed value so the DB compares it correctly."""

    if cursor_kind == "integer":
        try:
            return int(last_cursor)
        except ValueError:
            return last_cursor
    if cursor_kind == "timestamp":
        try:
            return datetime.fromisoformat(last_cursor)
        except ValueError:
            return last_cursor
    return last_cursor


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _rows_to_arrow(columns: list[str], rows: list) -> pa.Table:
    data = {name: [row[index] for row in rows] for index, name in enumerate(columns)}
    return pa.table(data)


def _max_cursor(columns: list[str], rows: list, cursor_column: str | None) -> str | None:
    if not cursor_column or not rows or cursor_column not in columns:
        return None
    index = columns.index(cursor_column)
    values = [row[index] for row in rows if row[index] is not None]
    if not values:
        return None
    maximum = max(values)
    return maximum.isoformat() if hasattr(maximum, "isoformat") else str(maximum)
