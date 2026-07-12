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
    SemanticAlias,
    SemanticLayer,
)

_CURRENCY_HINTS = ("amount", "revenue", "price", "total", "cost", "value", "balance")
_AVERAGE_HINTS = (
    "avg",
    "average",
    "percent",
    "percentage",
    "rate",
    "ratio",
    "margin",
    "score",
)
_ADDITIVE_HINTS = (
    "amount",
    "revenue",
    "sales",
    "count",
    "orders",
    "trips",
    "rides",
    "bookings",
    "borrows",
    "loans",
    "units",
    "quantity",
    "qty",
    "total",
)
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
    added_aliases: list[str] = field(default_factory=list)


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

    _add_aliases(layer, result)
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
    analytical_source = _is_analysis_ready_source(detail)
    countable_source = _is_countable_source(detail)
    time_column = _first_timestamp(detail)
    qualified_time = f"{summary.name}.{time_column}" if time_column else None

    count_name = f"{_singularize(summary.name)}_count" if countable_source else ""
    if count_name and count_name not in layer.metrics:
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

    if not analytical_source and summary.category not in {
        TableCategory.fact.value,
        TableCategory.event.value,
    }:
        return

    # Exclude primary keys and foreign keys — summing an id column is meaningless.
    key_columns = {c.name for c in detail.columns if c.is_primary_key}
    key_columns |= {col for rel in detail.outgoing for col in rel.source_columns}
    for column in detail.columns:
        if column.name in key_columns or not _is_numeric(column.data_type):
            continue
        if analytical_source and _looks_like_identifier(column.name):
            continue
        profile = profiles.get(f"{summary.schema}.{summary.name}.{column.name}")
        if profile is not None and profile.is_pii:
            continue
        # Avoid "total_total_amount" / "Total total amount": don't double the "total".
        base = column.name
        aggregate = _aggregate_for_column(base, analytical_source=analytical_source)
        metric_name = _metric_name(base, aggregate)
        if metric_name in layer.metrics:
            continue
        label = _metric_label(base, aggregate)
        layer.metrics[metric_name] = Metric(
            label=label,
            expression=f"{aggregate}({summary.name}.{column.name})",
            source_table=summary.qualified_name,
            time_column=qualified_time,
            status=MetricStatus.suggested,
            confidence=0.75 if analytical_source else 0.7,
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


def _add_aliases(layer: SemanticLayer, result: GenerationResult) -> None:
    """Add safe routing aliases for existing metrics and dimensions.

    The aliases never invent new semantics: every alias points at an existing metric/dimension
    and carries evidence that cites the existing semantic object.
    """

    for name, metric in layer.metrics.items():
        evidence = [f"metric:{name}", f"expression:{metric.expression}"]
        base_confidence = min(0.95, max(0.55, metric.confidence))
        candidates = {
            _human_phrase(name): base_confidence,
            _human_phrase(metric.label): base_confidence,
        }
        for alias, confidence in _metric_alias_candidates(name, metric, base_confidence).items():
            candidates[alias] = max(candidates.get(alias, 0.0), confidence)
        for alias, confidence in candidates.items():
            _add_alias(
                layer,
                result,
                phrase=alias,
                target=name,
                target_type="metric",
                confidence=confidence,
                evidence=evidence,
            )

    for name, dimension in layer.dimensions.items():
        evidence = [f"dimension:{name}", f"source:{dimension.source}"]
        candidates = {_human_phrase(name): 0.82}
        if dimension.label:
            candidates[_human_phrase(dimension.label)] = 0.86
        source_col = dimension.source.split(".")[-1]
        source_phrase = _human_phrase(source_col)
        candidates[source_phrase] = max(candidates.get(source_phrase, 0), 0.78)
        for alias, confidence in candidates.items():
            _add_alias(
                layer,
                result,
                phrase=alias,
                target=name,
                target_type="dimension",
                confidence=confidence,
                evidence=evidence,
            )


def _metric_alias_candidates(name: str, metric: Metric, confidence: float) -> dict[str, float]:
    aliases: dict[str, float] = {}
    tokens = _semantic_tokens(name)
    label_tokens = _semantic_tokens(metric.label)

    for source in (tokens, label_tokens):
        if not source:
            continue
        stripped = _strip_aggregate_prefix(source)
        if stripped and stripped != source:
            aliases[" ".join(stripped)] = max(aliases.get(" ".join(stripped), 0), confidence - 0.03)

        # COUNT(*) metrics over plural table names: sales_orders -> sales order count.
        if source[-1] == "count" and len(source) > 1:
            aliases[" ".join(source[:-1] + ["count"])] = max(
                aliases.get(" ".join(source[:-1] + ["count"]), 0), confidence
            )
            if source[-2:] == ["order", "count"]:
                score = confidence + (0.08 if "sales" in source else 0.0)
                if "purchase" in source:
                    score = confidence - 0.04
                aliases["order count"] = max(aliases.get("order count", 0), score)

        # Measures like completed_orders are frequently asked as "order count".
        if source[-1] in {"orders", "order"} or (
            len(source) > 1 and source[-2:] in (["completed", "orders"], ["completed", "order"])
        ):
            aliases["order count"] = max(aliases.get("order count", 0), confidence + 0.07)
            aliases["orders count"] = max(aliases.get("orders count", 0), confidence + 0.04)
        if source[-1] in {"items", "item"} and "order" in source:
            aliases["order item count"] = max(aliases.get("order item count", 0), confidence + 0.03)

    return {alias: min(0.95, score) for alias, score in aliases.items() if alias}


def _add_alias(
    layer: SemanticLayer,
    result: GenerationResult,
    *,
    phrase: str,
    target: str,
    target_type: str,
    confidence: float,
    evidence: list[str],
) -> None:
    normalized = _human_phrase(phrase)
    if not normalized:
        return
    existing = layer.aliases.get(normalized)
    if existing is not None and existing.confidence >= confidence:
        return
    layer.aliases[normalized] = SemanticAlias(
        target=target,
        target_type=target_type,
        confidence=round(confidence, 3),
        evidence=evidence,
        status=MetricStatus.suggested,
    )
    if normalized not in result.added_aliases:
        result.added_aliases.append(normalized)


def _first_timestamp(detail: TableDetail) -> str | None:
    for column in detail.columns:
        if column.data_type.lower().startswith(_TIMESTAMP_PREFIXES):
            return column.name
    return None


def _is_numeric(data_type: str) -> bool:
    return data_type.lower().startswith(_NUMERIC_PREFIXES)


def _is_text(data_type: str) -> bool:
    return data_type.lower().startswith(_TEXT_PREFIXES)


def _is_analysis_ready_source(detail: TableDetail) -> bool:
    """Return true for tables/views that already look like an analytical model.

    This is intentionally shape-based, not name-based. Many teams create reporting views for
    their own domain: library circulation by branch/genre, rides by city/driver tier, inventory
    by supplier/category, and so on. If a relation has categorical columns plus numeric measures,
    it can produce useful semantic metrics even when the table classifier calls it ``unknown``.
    """

    if detail.summary.category in {TableCategory.fact.value, TableCategory.event.value}:
        return True
    if detail.summary.category not in {TableCategory.unknown.value, TableCategory.snapshot.value}:
        return False
    numeric = [
        c for c in detail.columns if _is_numeric(c.data_type) and not _looks_like_identifier(c.name)
    ]
    text = [c for c in detail.columns if _is_text(c.data_type)]
    measure_like = [c for c in numeric if _measure_score(c.name) > 0]
    return len(text) >= 1 and len(measure_like) >= 2


def _is_countable_source(detail: TableDetail) -> bool:
    """Return true when COUNT(*) is a useful business metric for this table."""

    if detail.summary.kind != TableKind.table.value:
        return False
    if detail.summary.category in {TableCategory.bridge.value, TableCategory.configuration.value}:
        return False
    if detail.summary.category in {
        TableCategory.fact.value,
        TableCategory.event.value,
        TableCategory.dimension.value,
        TableCategory.snapshot.value,
        TableCategory.unknown.value,
    }:
        return bool([c for c in detail.columns if c.is_primary_key])
    return False


def _looks_like_identifier(column_name: str) -> bool:
    lowered = column_name.lower()
    return lowered == "id" or lowered.endswith("_id")


def _measure_score(column_name: str) -> int:
    lowered = column_name.lower()
    return sum(
        1 for hint in (*_AVERAGE_HINTS, *_ADDITIVE_HINTS, *_CURRENCY_HINTS) if hint in lowered
    )


def _aggregate_for_column(column_name: str, *, analytical_source: bool) -> str:
    lowered = column_name.lower()
    if any(hint in lowered for hint in _AVERAGE_HINTS):
        return "AVG"
    if (
        analytical_source
        and "price" in lowered
        and not any(hint in lowered for hint in ("revenue", "sales", "amount", "total"))
    ):
        return "AVG"
    return "SUM"


def _metric_name(column_name: str, aggregate: str) -> str:
    lowered = column_name.lower()
    if lowered.startswith(("total_", "sum_", "avg_", "average_")):
        return lowered
    prefix = "avg" if aggregate == "AVG" else "total"
    return f"{prefix}_{lowered}"


def _metric_label(column_name: str, aggregate: str) -> str:
    lowered = column_name.lower()
    if lowered.startswith(("total_", "sum_", "avg_", "average_")):
        return _humanize(column_name)
    prefix = "Average" if aggregate == "AVG" else "Total"
    return f"{prefix} {column_name.replace('_', ' ')}"


def _metric_format(column_name: str) -> MetricFormat:
    lowered = column_name.lower()
    if any(hint in lowered for hint in ("percent", "percentage", "rate", "ratio")):
        return MetricFormat.percent
    if any(hint in lowered for hint in _CURRENCY_HINTS):
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


def _human_phrase(text: str) -> str:
    return " ".join(_semantic_tokens(text))


def _semantic_tokens(text: str) -> list[str]:
    import re

    return [token for token in re.sub(r"[^a-z0-9]+", " ", text.lower()).split() if token]


def _strip_aggregate_prefix(tokens: list[str]) -> list[str]:
    if tokens and tokens[0] in {"total", "sum", "avg", "average"}:
        return tokens[1:]
    return tokens
