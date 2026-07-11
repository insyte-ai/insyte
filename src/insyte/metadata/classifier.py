"""Deterministic table classification (spec §10).

Given a scanned table and the relationships around it, assign an analytical category. This is
a heuristic aid, not ground truth — every result carries a confidence and the category is
stored as a suggestion the user can override later.
"""

from __future__ import annotations

from insyte.metadata.models import Relationship, ScannedTable, TableCategory, TableKind

_TIMESTAMP_TYPES = ("timestamp", "timestamptz", "date", "datetime")
_TIMESTAMP_NAME_HINTS = ("_at", "_date", "_time", "created", "updated", "occurred")
_CONFIG_NAME_HINTS = ("config", "setting", "option", "parameter", "feature_flag", "flag")


def _has_timestamp(table: ScannedTable) -> bool:
    for col in table.columns:
        type_base = col.data_type.lower()
        if any(type_base.startswith(t) for t in _TIMESTAMP_TYPES):
            return True
        if any(hint in col.name.lower() for hint in _TIMESTAMP_NAME_HINTS):
            return True
    return False


def _numeric_measure_count(table: ScannedTable, key_columns: set[str]) -> int:
    numeric_prefixes = (
        "numeric",
        "decimal",
        "double",
        "real",
        "money",
        "float",
        "integer",
        "int",
        "bigint",
        "smallint",
    )
    count = 0
    for col in table.columns:
        if col.name in key_columns or col.is_primary_key:
            continue
        if col.data_type.lower().startswith(numeric_prefixes):
            count += 1
    return count


def classify_table(
    table: ScannedTable,
    outgoing: list[Relationship],
    incoming: list[Relationship],
) -> tuple[TableCategory, float]:
    """Classify a table using its relationships, columns and keys."""

    if table.kind is TableKind.view:
        return TableCategory.unknown, 0.3

    outgoing_targets = {(r.target_schema, r.target_table) for r in outgoing}
    fk_columns = {c for r in outgoing for c in r.source_columns}
    pk_columns = set(table.primary_key_columns)
    num_fk_targets = len(outgoing_targets)
    is_referenced = len(incoming) > 0
    has_ts = _has_timestamp(table)
    measures = _numeric_measure_count(table, fk_columns | pk_columns)

    # Bridge / junction: links two+ entities and its key is made of those foreign keys.
    if num_fk_targets >= 2 and pk_columns and pk_columns <= fk_columns:
        return TableCategory.bridge, 0.85

    # Configuration: name hints and no incoming/outgoing relationships.
    if any(hint in table.name.lower() for hint in _CONFIG_NAME_HINTS) and not is_referenced:
        return TableCategory.configuration, 0.6

    # Fact: references other entities AND carries numeric measures (transactional record).
    if num_fk_targets >= 1 and measures >= 1:
        confidence = 0.6 + min(0.1 * num_fk_targets, 0.3)
        return TableCategory.fact, round(min(confidence, 0.9), 2)

    # Dimension: a referenced entity that describes rather than measures. Catches both root
    # entities (no foreign keys, e.g. products) and lightly-linked ones (e.g. customer -> city).
    if is_referenced and (measures == 0 or num_fk_targets == 0):
        return TableCategory.dimension, 0.75

    # Event: has a timestamp, is not referenced, and links to at most one entity.
    if has_ts and not is_referenced and num_fk_targets <= 1:
        return TableCategory.event, 0.55

    return TableCategory.unknown, 0.3
