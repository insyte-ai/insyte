"""Deterministic retrieval and capability metadata for the semantic layer.

Retrieval narrows model context but never authorizes an object: callers must validate the
model's answer against the complete semantic layer. This keeps fuzzy matching useful without
letting it manufacture schema objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from insyte.metadata.models import CardinalityCategory, ColumnProfile, RelationshipInfo
from insyte.semantic.models import Dimension, SemanticLayer

_STOP_WORDS = {
    "a",
    "an",
    "and",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "the",
    "to",
    "what",
    "which",
    "why",
}
_GOOD_DIMENSION_CARDINALITY = {
    CardinalityCategory.low,
    CardinalityCategory.medium,
    CardinalityCategory.constant,
}


@dataclass(frozen=True)
class CatalogCandidate:
    object_type: str
    name: str
    score: float
    matched_by: tuple[str, ...]


@dataclass(frozen=True)
class MetricCapability:
    metric: str
    source_table: str
    time_column: str | None
    dimensions: tuple[str, ...] = ()
    data_start: datetime | None = None
    data_end: datetime | None = None
    evidence: tuple[str, ...] = ()


@dataclass
class SemanticCatalog:
    layer: SemanticLayer
    profiles: list[ColumnProfile] = field(default_factory=list)
    relationships: list[RelationshipInfo] = field(default_factory=list)

    def candidates(
        self, question: str, *, metric_limit: int = 8, dimension_limit: int = 12
    ) -> list[CatalogCandidate]:
        """Return explainable ranked candidates using only known semantic objects."""

        query = _normalise(question)
        query_tokens = _tokens(question)
        candidates: list[CatalogCandidate] = []
        for name, metric in self.layer.metrics.items():
            text = " ".join((name, metric.label, metric.source_table, metric.expression))
            score, reasons = _score(query, query_tokens, name, metric.label, text)
            alias_score, alias_reasons = self._alias_score(query, query_tokens, name, "metric")
            if alias_score > score:
                score, reasons = alias_score, alias_reasons
            if score > 0:
                candidates.append(CatalogCandidate("metric", name, score, tuple(reasons)))
        for name, dimension in self.layer.dimensions.items():
            label = dimension.label or name
            profile = self._profile(dimension)
            profile_text = ""
            if profile and not profile.is_pii:
                profile_text = " ".join(value for value, _ in profile.top_values[:10])
            text = " ".join((name, label, dimension.source, profile_text))
            score, reasons = _score(query, query_tokens, name, label, text)
            alias_score, alias_reasons = self._alias_score(query, query_tokens, name, "dimension")
            if alias_score > score:
                score, reasons = alias_score, alias_reasons
            if score > 0:
                candidates.append(CatalogCandidate("dimension", name, score, tuple(reasons)))
        candidates.sort(key=lambda item: (-item.score, item.object_type, item.name))
        metrics = [item for item in candidates if item.object_type == "metric"][:metric_limit]
        dimensions = [item for item in candidates if item.object_type == "dimension"][
            :dimension_limit
        ]
        return metrics + dimensions

    def narrowed_layer(self, question: str) -> tuple[SemanticLayer, list[CatalogCandidate]]:
        """Build a small prompt layer; return the full layer when retrieval has no signal."""

        candidates = self.candidates(question)
        metric_names = {item.name for item in candidates if item.object_type == "metric"}
        dimension_names = {item.name for item in candidates if item.object_type == "dimension"}
        if not metric_names:
            return self.layer, candidates

        source_tables = {self.layer.metrics[name].source_table for name in metric_names}
        # Include same-table dimensions as safe planning context even if the question only names
        # the metric. Explicitly matched dimensions remain available across validated joins.
        for name, dimension in self.layer.dimensions.items():
            if _qualified(dimension.table) in {_qualified(table) for table in source_tables}:
                dimension_names.add(name)
        aliases = {
            phrase: alias
            for phrase, alias in self.layer.aliases.items()
            if (alias.target_type == "metric" and alias.target in metric_names)
            or (alias.target_type == "dimension" and alias.target in dimension_names)
        }
        entities = {
            name: entity
            for name, entity in self.layer.entities.items()
            if _qualified(entity.table) in {_qualified(table) for table in source_tables}
        }
        return (
            SemanticLayer(
                metrics={name: self.layer.metrics[name] for name in metric_names},
                dimensions={name: self.layer.dimensions[name] for name in dimension_names},
                entities=entities,
                aliases=aliases,
            ),
            candidates,
        )

    def capability(self, metric_name: str) -> MetricCapability | None:
        metric = self.layer.metrics.get(metric_name)
        if metric is None:
            return None
        source = _qualified(metric.source_table)
        reachable = {source}
        for relationship in self.relationships:
            left = _qualified(f"{relationship.source_schema}.{relationship.source_table}")
            right = _qualified(f"{relationship.target_schema}.{relationship.target_table}")
            if left == source:
                reachable.add(right)
            if right == source:
                reachable.add(left)

        dimensions: list[tuple[int, str]] = []
        evidence: list[str] = []
        for name, dimension in self.layer.dimensions.items():
            table = _qualified(dimension.table)
            if table not in reachable:
                continue
            profile = self._profile(dimension)
            blocked_cardinality = {
                CardinalityCategory.unique,
                CardinalityCategory.high,
                CardinalityCategory.empty,
            }
            if profile and (profile.is_pii or profile.cardinality in blocked_cardinality):
                continue
            priority = 0 if table == source else 1
            if profile and profile.cardinality not in _GOOD_DIMENSION_CARDINALITY:
                priority += 1
            dimensions.append((priority, name))
        dimensions.sort()

        start = end = None
        if metric.time_column:
            time_profile = self._profile_source(metric.time_column, metric.source_table)
            if time_profile:
                start = _parse_datetime(time_profile.min_value)
                end = _parse_datetime(time_profile.max_value)
                if start or end:
                    evidence.append("profiled time-column coverage")
        if dimensions:
            evidence.append("dimensions filtered by join reachability and cardinality")
        return MetricCapability(
            metric=metric_name,
            source_table=metric.source_table,
            time_column=metric.time_column,
            dimensions=tuple(name for _, name in dimensions),
            data_start=start,
            data_end=end,
            evidence=tuple(evidence),
        )

    def _alias_score(
        self, query: str, query_tokens: set[str], target: str, target_type: str
    ) -> tuple[float, list[str]]:
        best = 0.0
        reasons: list[str] = []
        for phrase, alias in self.layer.aliases.items():
            if alias.target != target or alias.target_type != target_type:
                continue
            normalised = _normalise(phrase)
            overlap = len(query_tokens & _tokens(phrase))
            score = overlap * 2.0 * alias.confidence
            if normalised and normalised in query:
                score += 8.0 * alias.confidence
            if score > best:
                best = score
                reasons = [f"alias:{phrase}"]
        return best, reasons

    def _profile(self, dimension: Dimension) -> ColumnProfile | None:
        return self._profile_source(dimension.source, dimension.table)

    def _profile_source(self, source: str, default_table: str) -> ColumnProfile | None:
        parts = source.split(".")
        column = parts[-1]
        table = ".".join(parts[:-1]) or default_table
        wanted = _qualified(table)
        for profile in self.profiles:
            profile_table = _qualified(f"{profile.schema}.{profile.table}")
            if profile_table == wanted and profile.column == column:
                return profile
        return None


def _score(
    query: str, query_tokens: set[str], name: str, label: str, searchable: str
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    for value, reason, weight in ((name, "name", 8.0), (label, "label", 7.0)):
        phrase = _normalise(value)
        if phrase and phrase in query:
            score += weight
            reasons.append(reason)
    overlap = query_tokens & _tokens(searchable)
    if overlap:
        score += float(len(overlap) * 2)
        reasons.append("tokens:" + ",".join(sorted(overlap)))
    return score, reasons


def _normalise(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _tokens(value: str) -> set[str]:
    tokens = {token for token in _normalise(value).split() if token not in _STOP_WORDS}
    tokens.update(token[:-1] for token in list(tokens) if token.endswith("s") and len(token) > 3)
    return tokens


def _qualified(table: str) -> str:
    return table if "." in table else f"public.{table}"


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
