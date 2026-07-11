"""Auto-generate suggested semantic entities, metrics and dimensions (spec §12).

Suggestions are derived from scanned schema plus profiles, and are always marked
``suggested`` until a user confirms them with ``insyte metrics approve``. Generation merges
into any existing semantic layer without overwriting entries the user has already defined.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from insyte.metadata.models import ColumnProfile, TableCategory, TableDetail, TableKind
from insyte.semantic.models import (
    Dimension,
    Entity,
    Metric,
    MetricFormat,
    MetricStatus,
    SemanticLayer,
)

_CURRENCY_HINTS = ("amount", "revenue", "price", "total", "cost", "value", "balance")
_TIMESTAMP_PREFIXES = ("timestamp", "date", "datetime")
_NUMERIC_PREFIXES = (
    "numeric",
    "decimal",
    "double",
    "real",
    "money",
    "float",
    "int",
    "bigint",
    "smallint",
)
_TEXT_PREFIXES = ("char", "text", "citext", "varchar")


@dataclass
class GenerationResult:
    layer: SemanticLayer
    added_entities: list[str] = field(default_factory=list)
    added_metrics: list[str] = field(default_factory=list)
    added_dimensions: list[str] = field(default_factory=list)


def generate_semantic(
    tables: list[TableDetail],
    profiles: dict[str, ColumnProfile],
    existing: SemanticLayer,
) -> GenerationResult:
    """Merge suggested entities/metrics/dimensions into ``existing`` and report what was added."""

    layer = existing.model_copy(deep=True)
    result = GenerationResult(layer=layer)

    for detail in tables:
        _add_entity(detail, layer, result)
        _add_metrics(detail, profiles, layer, result)
        _add_dimensions(detail, profiles, layer, result)

    return result


def _add_entity(detail: TableDetail, layer: SemanticLayer, result: GenerationResult) -> None:
    summary = detail.summary
    if summary.kind != TableKind.table.value:
        return
    pk = [c.name for c in detail.columns if c.is_primary_key]
    if len(pk) != 1:
        return
    name = _singularize(summary.name)
    if name in layer.entities:
        return
    layer.entities[name] = Entity(
        table=summary.qualified_name,
        primary_key=pk[0],
        time_column=_first_timestamp(detail),
        confidence=0.9,
    )
    result.added_entities.append(name)


def _add_metrics(
    detail: TableDetail,
    profiles: dict[str, ColumnProfile],
    layer: SemanticLayer,
    result: GenerationResult,
) -> None:
    summary = detail.summary
    if summary.category not in {TableCategory.fact.value, TableCategory.event.value}:
        return
    time_column = _first_timestamp(detail)
    qualified_time = f"{summary.name}.{time_column}" if time_column else None

    count_name = f"{_singularize(summary.name)}_count"
    if count_name not in layer.metrics:
        layer.metrics[count_name] = Metric(
            label=_humanize(count_name),
            expression="COUNT(*)",
            source_table=summary.qualified_name,
            time_column=qualified_time,
            status=MetricStatus.suggested,
            confidence=0.85,
            format=MetricFormat.number,
        )
        result.added_metrics.append(count_name)

    # Exclude primary keys and foreign keys — summing an id column is meaningless.
    key_columns = {c.name for c in detail.columns if c.is_primary_key}
    key_columns |= {col for rel in detail.outgoing for col in rel.source_columns}
    for column in detail.columns:
        if column.name in key_columns or not _is_numeric(column.data_type):
            continue
        profile = profiles.get(f"{summary.schema}.{summary.name}.{column.name}")
        if profile is not None and profile.is_pii:
            continue
        # Avoid "total_total_amount" / "Total total amount": don't double the "total".
        base = column.name
        metric_name = base if base.startswith(("total_", "sum_")) else f"total_{base}"
        if metric_name in layer.metrics:
            continue
        label = _humanize(base) if "total" in base else f"Total {base.replace('_', ' ')}"
        layer.metrics[metric_name] = Metric(
            label=label,
            expression=f"SUM({summary.name}.{column.name})",
            source_table=summary.qualified_name,
            time_column=qualified_time,
            status=MetricStatus.suggested,
            confidence=0.7,
            format=_metric_format(column.name),
        )
        result.added_metrics.append(metric_name)


def _add_dimensions(
    detail: TableDetail,
    profiles: dict[str, ColumnProfile],
    layer: SemanticLayer,
    result: GenerationResult,
) -> None:
    summary = detail.summary
    key_columns = {c.name for c in detail.columns if c.is_primary_key}
    for column in detail.columns:
        if column.name in key_columns or not _is_text(column.data_type):
            continue
        profile = profiles.get(f"{summary.schema}.{summary.name}.{column.name}")
        if profile is not None and (profile.is_pii or profile.cardinality.value in {"unique"}):
            continue
        dimension_name = column.name
        if dimension_name in layer.dimensions:
            dimension_name = f"{summary.name}_{column.name}"
        if dimension_name in layer.dimensions:
            continue
        layer.dimensions[dimension_name] = Dimension(
            source=f"{summary.name}.{column.name}", type="categorical"
        )
        result.added_dimensions.append(dimension_name)


def _first_timestamp(detail: TableDetail) -> str | None:
    for column in detail.columns:
        if column.data_type.lower().startswith(_TIMESTAMP_PREFIXES):
            return column.name
    return None


def _is_numeric(data_type: str) -> bool:
    return data_type.lower().startswith(_NUMERIC_PREFIXES)


def _is_text(data_type: str) -> bool:
    return data_type.lower().startswith(_TEXT_PREFIXES)


def _metric_format(column_name: str) -> MetricFormat:
    if any(hint in column_name.lower() for hint in _CURRENCY_HINTS):
        return MetricFormat.currency
    return MetricFormat.number


def _singularize(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith("ies") and len(lowered) > 3:
        return lowered[:-3] + "y"
    # "-es" plurals where dropping just "s" would be wrong: addresses->address, boxes->box.
    for suffix in ("sses", "shes", "ches", "xes", "zes"):
        if lowered.endswith(suffix):
            return lowered[:-2]
    if lowered.endswith("s") and not lowered.endswith("ss"):
        return lowered[:-1]
    return lowered


def _humanize(name: str) -> str:
    """Turn a snake_case identifier into a readable label ('grand_total' -> 'Grand total')."""

    return name.replace("_", " ").strip().capitalize()
