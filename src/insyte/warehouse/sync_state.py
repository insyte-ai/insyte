"""Determine the incremental cursor for a table (spec §16)."""

from __future__ import annotations

from insyte.metadata.models import ColumnInfo

# Preferred timestamp cursor columns, in priority order.
_TIMESTAMP_CURSORS = ("updated_at", "modified_at", "created_at")
_TIMESTAMP_PREFIXES = ("timestamp", "date", "datetime")
_INTEGER_PREFIXES = ("integer", "int", "bigint", "smallint", "serial", "bigserial")


def detect_cursor(columns: list[ColumnInfo]) -> tuple[str | None, str | None]:
    """Return ``(cursor_column, kind)`` for incremental sync, or ``(None, None)``.

    Prefers a timestamp column (updated_at → modified_at → created_at), then falls back to a
    single-column integer primary key (a sequential id).
    """

    by_name = {c.name.lower(): c for c in columns}
    for candidate in _TIMESTAMP_CURSORS:
        column = by_name.get(candidate)
        if column is not None and _is_timestamp(column.data_type):
            return column.name, "timestamp"

    primary_keys = [c for c in columns if c.is_primary_key]
    if len(primary_keys) == 1 and _is_integer(primary_keys[0].data_type):
        return primary_keys[0].name, "integer"

    return None, None


def _is_timestamp(data_type: str) -> bool:
    return data_type.lower().startswith(_TIMESTAMP_PREFIXES)


def _is_integer(data_type: str) -> bool:
    return data_type.lower().startswith(_INTEGER_PREFIXES)
