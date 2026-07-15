"""Safe, sample-based column profiling (spec §11).

Profiling reads at most ``sample_rows`` rows per table, inside the connector's read-only,
timeout-bounded transaction — never an unrestricted full-table scan. Statistics are computed
in Python over the bounded sample. PII columns are detected and their sample-derived values
(top values, min/max) are masked before they are stored or shown.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from insyte.config.models import ProfilingSection
from insyte.connectors.base import DatabaseConnector
from insyte.logging_config import get_logger
from insyte.metadata.models import (
    CardinalityCategory,
    ColumnProfile,
    ProfileResult,
    TableProfile,
)
from insyte.metadata.pii_detector import classify_column, mask_value
from insyte.metadata.repository import MetadataRepository

logger = get_logger("metadata.profiler")

_TOP_VALUES = 5


class Profiler:
    """Profiles scanned tables using a bounded per-table sample."""

    def __init__(
        self,
        connector: DatabaseConnector,
        metadata: MetadataRepository,
        profiling: ProfilingSection,
    ) -> None:
        self._connector = connector
        self._metadata = metadata
        self._profiling = profiling

    def profile(self) -> ProfileResult:
        tables = self._metadata.list_table_details(self._profiling.maximum_tables)
        table_profiles: list[TableProfile] = []
        column_profiles: list[ColumnProfile] = []

        logger.info("profile_started", extra={"tables": len(tables)})
        with self._connector.read_only_transaction() as conn:
            for detail in tables:
                summary = detail.summary
                column_names = [c.name for c in detail.columns][
                    : self._profiling.maximum_columns_per_table
                ]
                types = {c.name: c.data_type for c in detail.columns}
                if not column_names:
                    continue
                sampled = self._sample(conn, summary.schema, summary.name, column_names)
                if sampled is None:
                    continue
                table_profiles.append(
                    TableProfile(
                        schema=summary.schema,
                        table=summary.name,
                        row_estimate=summary.row_estimate,
                        sampled_rows=len(sampled),
                        column_count=len(column_names),
                    )
                )
                for index, name in enumerate(column_names):
                    values = [row[index] for row in sampled]
                    column_profiles.append(
                        build_column_profile(
                            summary.schema,
                            summary.name,
                            name,
                            types[name],
                            values,
                            len(sampled),
                            detect_pii=self._profiling.detect_pii,
                        )
                    )

        logger.info(
            "profile_completed",
            extra={"tables": len(table_profiles), "columns": len(column_profiles)},
        )
        return ProfileResult(table_profiles=table_profiles, column_profiles=column_profiles)

    def _sample(
        self, conn: Connection, schema: str, table: str, columns: list[str]
    ) -> list[tuple[object, ...]] | None:
        select = ", ".join(_quote(c) for c in columns)
        sql = (
            f"SELECT {select} FROM {_quote(schema)}.{_quote(table)} "
            f"LIMIT {int(self._profiling.sample_rows)}"
        )
        try:
            return [tuple(row) for row in conn.execute(text(sql))]
        except SQLAlchemyError:
            logger.info("profile_sample_failed", extra={"table": f"{schema}.{table}"})
            return None


def build_column_profile(
    schema: str,
    table: str,
    name: str,
    data_type: str,
    values: list[object],
    sampled_rows: int,
    *,
    detect_pii: bool,
) -> ColumnProfile:
    """Compute a column profile from a bounded sample of values (pure — no database)."""

    total = len(values)
    non_null = [v for v in values if v is not None]
    null_fraction = round((total - len(non_null)) / total, 4) if total else 0.0
    distinct = len({_hashable(v) for v in non_null})
    duplicate_ratio = round(1 - (distinct / len(non_null)), 4) if non_null else 0.0
    cardinality = _cardinality(distinct, len(non_null))

    classification = classify_column(name, data_type, non_null, detect_pii=detect_pii)
    min_value, max_value, avg_value = _extremes(non_null, masked=classification.is_pii)
    top_values = _top_values(non_null, masked=classification.is_pii)

    return ColumnProfile(
        schema=schema,
        table=table,
        column=name,
        null_fraction=null_fraction,
        distinct_estimate=distinct,
        duplicate_ratio=duplicate_ratio,
        cardinality=cardinality,
        sampled_rows=sampled_rows,
        min_value=min_value,
        max_value=max_value,
        avg_value=avg_value,
        top_values=top_values,
        is_pii=classification.is_pii,
        pii_type=classification.pii_type.value if classification.pii_type else None,
        pii_confidence=classification.confidence,
    )


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _hashable(value: object) -> object:
    try:
        hash(value)
    except TypeError:
        return str(value)
    return value


def _cardinality(distinct: int, non_null: int) -> CardinalityCategory:
    if non_null == 0:
        return CardinalityCategory.empty
    if distinct <= 1:
        return CardinalityCategory.constant
    if distinct == non_null:
        return CardinalityCategory.unique
    ratio = distinct / non_null
    if ratio > 0.5:
        return CardinalityCategory.high
    if ratio > 0.1:
        return CardinalityCategory.medium
    return CardinalityCategory.low


def _extremes(
    non_null: list[object], *, masked: bool
) -> tuple[str | None, str | None, float | None]:
    if not non_null:
        return None, None, None
    numeric = [float(v) for v in non_null if isinstance(v, (int, float, Decimal))]
    avg = (
        round(sum(numeric) / len(numeric), 4) if numeric and len(numeric) == len(non_null) else None
    )
    try:
        low, high = min(non_null), max(non_null)  # type: ignore[type-var]
    except TypeError:
        return None, None, None if masked else avg
    if masked:
        return mask_value(low), mask_value(high), None
    return str(low), str(high), avg


def _top_values(non_null: list[object], *, masked: bool) -> list[tuple[str, int]]:
    counts = Counter(str(v) for v in non_null).most_common(_TOP_VALUES)
    if masked:
        return [(mask_value(value), count) for value, count in counts]
    return counts


def profile_index(result: ProfileResult) -> dict[str, ColumnProfile]:
    """Index column profiles by their qualified name (schema.table.column)."""

    return {profile.qualified_column: profile for profile in result.column_profiles}
