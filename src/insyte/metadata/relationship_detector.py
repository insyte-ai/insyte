"""Relationship detection: declared foreign keys plus inferred relationships.

Declared foreign keys are certain (confidence 1.0). Inferred relationships come from naming
conventions, type compatibility and target-column uniqueness, and are always marked
``inferred`` with a sub-1.0 confidence — they are suggestions, never treated as real
constraints (spec §10).
"""

from __future__ import annotations

from insyte.metadata.models import (
    Relationship,
    RelationshipKind,
    ScannedColumn,
    ScannedTable,
)

# Normalised type families for compatibility checks.
_INT_TYPES = {"integer", "int", "int4", "bigint", "int8", "smallint", "int2", "serial", "bigserial"}
_TEXT_TYPES = {"text", "varchar", "character varying", "char", "character", "citext"}

_MAX_INFERRED_CONFIDENCE = 0.95


def _normalise_type(data_type: str) -> str:
    base = data_type.lower().split("(")[0].strip()
    if base in _INT_TYPES:
        return "int"
    if base in _TEXT_TYPES:
        return "text"
    return base


def types_compatible(a: str, b: str) -> bool:
    """Whether two column types can plausibly join."""

    return _normalise_type(a) == _normalise_type(b)


def candidate_target_names(base: str) -> set[str]:
    """Return plausible table names for a foreign-key base (``customer`` -> customers …)."""

    base = base.lower()
    names = {base, f"{base}s", f"{base}es"}
    if base.endswith("y"):
        names.add(f"{base[:-1]}ies")
    if base.endswith("s"):
        names.add(base[:-1])
    return names


def _foreign_key_base(column_name: str) -> str | None:
    """Extract the entity base from a foreign-key-looking column, or None."""

    name = column_name.lower()
    if name.endswith("_id"):
        return name[:-3]
    if name.endswith("id") and len(name) > 2 and name != "id":
        return name[:-2]
    return None


def detect_relationships(tables: list[ScannedTable]) -> list[Relationship]:
    """Detect declared and inferred relationships across a set of scanned tables."""

    by_name: dict[str, ScannedTable] = {t.name.lower(): t for t in tables}
    relationships: list[Relationship] = []
    declared: set[tuple[str, str, tuple[str, ...]]] = set()

    # 1. Declared foreign keys — certain.
    for table in tables:
        for fk in table.foreign_keys:
            relationships.append(
                Relationship(
                    source_schema=table.schema,
                    source_table=table.name,
                    source_columns=list(fk.columns),
                    target_schema=fk.target_schema,
                    target_table=fk.target_table,
                    target_columns=list(fk.target_columns),
                    kind=RelationshipKind.foreign_key,
                    confidence=1.0,
                    constraint_name=fk.name,
                )
            )
            declared.add((table.schema, table.name, tuple(fk.columns)))

    # 2. Inferred relationships from single-column foreign-key-looking columns.
    for table in tables:
        for column in table.columns:
            if column.is_primary_key and column.name.lower() == "id":
                continue
            if (table.schema, table.name, (column.name,)) in declared:
                continue
            inferred = _infer_for_column(table, column, by_name)
            if inferred is not None:
                relationships.append(inferred)

    return relationships


def _infer_for_column(
    table: ScannedTable,
    column: ScannedColumn,
    by_name: dict[str, ScannedTable],
) -> Relationship | None:
    base = _foreign_key_base(column.name)
    if base is None:
        return None

    for candidate in candidate_target_names(base):
        target = by_name.get(candidate)
        if target is None or target.qualified_name == table.qualified_name:
            continue
        target_column = _pick_target_column(target)
        if target_column is None:
            continue
        if not types_compatible(column.data_type, target_column.data_type):
            continue
        confidence = _score(column, target_column, base, target)
        return Relationship(
            source_schema=table.schema,
            source_table=table.name,
            source_columns=[column.name],
            target_schema=target.schema,
            target_table=target.name,
            target_columns=[target_column.name],
            kind=RelationshipKind.inferred,
            confidence=round(confidence, 2),
            constraint_name=None,
        )
    return None


def _pick_target_column(target: ScannedTable) -> ScannedColumn | None:
    """Choose the column an inferred FK would point at: a single-column PK, else 'id'."""

    if len(target.primary_key_columns) == 1:
        pk_name = target.primary_key_columns[0]
        for col in target.columns:
            if col.name == pk_name:
                return col
    for col in target.columns:
        if col.name.lower() == "id":
            return col
    return None


def _score(
    column: ScannedColumn,
    target_column: ScannedColumn,
    base: str,
    target: ScannedTable,
) -> float:
    confidence = 0.5
    if target_column.is_primary_key:
        confidence += 0.25
    elif target_column.is_unique:
        confidence += 0.15
    if types_compatible(column.data_type, target_column.data_type):
        confidence += 0.1
    if target.name.lower() in {base, f"{base}s"}:
        confidence += 0.05
    return min(confidence, _MAX_INFERRED_CONFIDENCE)
