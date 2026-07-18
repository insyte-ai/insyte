"""Ground and persist AI-proposed derived metrics without accepting model-written SQL."""

from __future__ import annotations

import re
from dataclasses import dataclass

from insyte.metadata.models import ColumnProfile
from insyte.semantic.models import Metric, MetricStatus, SemanticAlias, SemanticLayer

_NAME = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_WORDS = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class DerivedMetricProposal:
    name: str
    label: str
    base_metric: str
    filter_column: str
    filter_values: tuple[str | int | float, ...]
    aliases: tuple[str, ...]
    assumption: str
    confidence: float
    evidence: tuple[str, ...]


def validate_metric_proposal(
    raw: object,
    layer: SemanticLayer,
    profiles: list[ColumnProfile],
    *,
    question: str,
) -> DerivedMetricProposal | None:
    """Validate a model proposal exclusively against existing semantic/profile evidence."""

    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip().lower()
    label = " ".join(str(raw.get("label") or "").split())
    base_name = str(raw.get("base_metric") or "").strip()
    column = str(raw.get("filter_column") or "").strip()
    values = raw.get("filter_values")
    assumption = " ".join(str(raw.get("assumption") or "").split())
    aliases_raw = raw.get("aliases")
    base = layer.metrics.get(base_name)
    profile = _profile(column, profiles)

    if (
        not _NAME.fullmatch(name)
        or name in layer.metrics
        or not label
        or base is None
        or profile is None
        or profile.is_pii
        or not _same_table(column, base.source_table)
        or not isinstance(values, list)
        or not 1 <= len(values) <= 20
        or not assumption
        or not isinstance(aliases_raw, list)
    ):
        return None

    observed = {str(value).strip().casefold() for value, _ in profile.top_values}
    if not observed or any(str(value).strip().casefold() not in observed for value in values):
        return None

    question_words = set(_WORDS.findall(question.casefold()))
    base_words = set(_WORDS.findall(f"{base_name} {base.label}".replace("_", " ").casefold()))
    aliases: list[str] = []
    for item in aliases_raw[:5]:
        alias = " ".join(str(item).strip().lower().split())
        alias_words = set(_WORDS.findall(alias))
        if alias and alias_words and alias_words <= question_words | base_words:
            aliases.append(alias)
    if not aliases:
        return None

    confidence = raw.get("confidence", 0.5)
    try:
        confidence_value = min(0.89, max(0.8, float(confidence)))
    except (TypeError, ValueError):
        return None
    evidence = (
        f"metric:{base_name}",
        f"profile:{profile.qualified_column}",
        "observed_values:" + ",".join(str(value) for value in values),
    )
    return DerivedMetricProposal(
        name=name,
        label=label,
        base_metric=base_name,
        filter_column=column,
        filter_values=tuple(values),
        aliases=tuple(dict.fromkeys(aliases)),
        assumption=assumption,
        confidence=confidence_value,
        evidence=evidence,
    )


def apply_metric_proposal(proposal: DerivedMetricProposal, layer: SemanticLayer) -> SemanticLayer:
    """Add a confirmation-required derived metric and grounded aliases to a copied layer."""

    result = layer.model_copy(deep=True)
    base = result.metrics[proposal.base_metric]
    filters = {**base.filters, proposal.filter_column: list(proposal.filter_values)}
    result.metrics[proposal.name] = Metric(
        label=proposal.label,
        expression=base.expression,
        source_table=base.source_table,
        filters=filters,
        time_column=base.time_column,
        status=MetricStatus.suggested,
        confidence=proposal.confidence,
        format=base.format,
        requires_confirmation=True,
        assumption=proposal.assumption,
        evidence=list(proposal.evidence),
    )
    for alias in proposal.aliases:
        result.aliases[alias] = SemanticAlias(
            target=proposal.name,
            target_type="metric",
            confidence=proposal.confidence,
            evidence=list(proposal.evidence),
            status=MetricStatus.suggested,
        )
    return result


def _profile(column: str, profiles: list[ColumnProfile]) -> ColumnProfile | None:
    parts = column.split(".")
    if len(parts) not in {2, 3}:
        return None
    table, name = parts[-2:]
    return next((item for item in profiles if item.table == table and item.column == name), None)


def _same_table(column: str, source_table: str) -> bool:
    return column.split(".")[-2] == source_table.split(".")[-1]
