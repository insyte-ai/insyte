"""Parse chat input into an intent.

Milestone 6 uses a small rule-based parser (slash commands + a metric mini-grammar) so the UI
is usable without an LLM. The richer natural-language layer arrives with the MCP/AI integration
in Milestone 7; this parser is deliberately deterministic and fully unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from insyte.analytics.models import TimeGrain
from insyte.semantic.models import SemanticLayer


class IntentKind(StrEnum):
    help = "help"
    clear = "clear"
    quit = "quit"
    metrics = "metrics"
    schema = "schema"
    table = "table"
    history = "history"
    analysis = "analysis"
    unknown = "unknown"


class AnalysisMode(StrEnum):
    aggregate = "aggregate"
    timeseries = "timeseries"
    segment = "segment"
    opportunity = "opportunity"
    compare = "compare"
    forecast = "forecast"


@dataclass
class Intent:
    kind: IntentKind
    mode: AnalysisMode | None = None
    metric: str | None = None
    secondary_metric: str | None = None
    grain: TimeGrain | None = None
    dimension: str | None = None
    argument: str | None = None
    raw: str = ""


_GRAIN_WORDS: dict[str, TimeGrain] = {
    "daily": TimeGrain.day,
    "day": TimeGrain.day,
    "days": TimeGrain.day,
    "weekly": TimeGrain.week,
    "week": TimeGrain.week,
    "weeks": TimeGrain.week,
    "monthly": TimeGrain.month,
    "month": TimeGrain.month,
    "months": TimeGrain.month,
    "quarterly": TimeGrain.quarter,
    "quarter": TimeGrain.quarter,
    "yearly": TimeGrain.year,
    "annual": TimeGrain.year,
    "annually": TimeGrain.year,
    "year": TimeGrain.year,
}

_SLASH = {
    "help": IntentKind.help,
    "clear": IntentKind.clear,
    "quit": IntentKind.quit,
    "exit": IntentKind.quit,
    "metrics": IntentKind.metrics,
    "schema": IntentKind.schema,
    "history": IntentKind.history,
}

_AUTO_ALIAS_CONFIDENCE = 0.8
_AMBIGUITY_MARGIN = 0.05


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _tokens(text: str) -> list[str]:
    return _normalize(text).split()


def parse_intent(text: str, layer: SemanticLayer) -> Intent:
    """Parse a line of chat input into an :class:`Intent`."""

    text = text.strip()
    if not text:
        return Intent(IntentKind.unknown, raw=text)
    if text.startswith("/"):
        return _parse_slash(text)
    return _parse_analysis(text, layer)


def _parse_slash(text: str) -> Intent:
    parts = text[1:].split(maxsplit=1)
    command = parts[0].lower() if parts else ""
    argument = parts[1].strip() if len(parts) > 1 else None
    if command == "table":
        return Intent(IntentKind.table, argument=argument, raw=text)
    kind = _SLASH.get(command)
    if kind is None:
        return Intent(IntentKind.unknown, argument=command, raw=text)
    return Intent(kind, raw=text)


def _parse_analysis(text: str, layer: SemanticLayer) -> Intent:
    tokens = _tokens(text)
    metric = find_metric(text, layer)
    if metric is None:
        return Intent(IntentKind.unknown, raw=text)

    mode: AnalysisMode | None = None
    grain: TimeGrain | None = None
    dimension: str | None = None

    if "compare" in tokens or "vs" in tokens:
        mode = AnalysisMode.compare

    if "by" in tokens:
        after = tokens[tokens.index("by") + 1 :]
        if after and after[0] in _GRAIN_WORDS:
            grain, mode = _GRAIN_WORDS[after[0]], AnalysisMode.timeseries
        elif after:
            found = find_dimension(after, layer)
            if found is not None:
                dimension, mode = found, AnalysisMode.segment

    if mode is None:
        for token in tokens:
            if token in _GRAIN_WORDS:
                grain, mode = _GRAIN_WORDS[token], AnalysisMode.timeseries
                break

    if mode is None:
        mode = AnalysisMode.aggregate
    if mode is AnalysisMode.compare and grain is None:
        grain = TimeGrain.month

    return Intent(
        IntentKind.analysis,
        mode=mode,
        metric=metric,
        grain=grain,
        dimension=dimension,
        raw=text,
    )


def find_metric(text: str, layer: SemanticLayer) -> str | None:
    """Find the best-matching metric name in ``text`` (longest phrase wins)."""

    haystack = f" {_normalize(text)} "
    best: str | None = None
    best_len = -1
    for name, metric in layer.metrics.items():
        for candidate in (name, name.replace("_", " "), metric.label):
            phrase = _normalize(candidate)
            if phrase and f" {phrase} " in haystack and len(phrase) > best_len:
                best, best_len = name, len(phrase)
    if best is not None:
        return best
    return _find_metric_alias(haystack, layer)


def _find_metric_alias(haystack: str, layer: SemanticLayer) -> str | None:
    matches: list[tuple[float, int, str]] = []
    for phrase, alias in layer.aliases.items():
        if alias.target_type != "metric" or alias.target not in layer.metrics:
            continue
        if layer.metrics[alias.target].requires_confirmation:
            continue
        normalized = _normalize(phrase)
        if not normalized or f" {normalized} " not in haystack:
            continue
        if alias.confidence < _AUTO_ALIAS_CONFIDENCE:
            continue
        matches.append((alias.confidence, len(normalized), alias.target))
    if not matches:
        return None
    matches.sort(reverse=True)
    if len(matches) > 1:
        top_confidence, _, top_target = matches[0]
        second_confidence, _, second_target = matches[1]
        if top_target != second_target and top_confidence - second_confidence < _AMBIGUITY_MARGIN:
            return None
    return matches[0][2]


def find_dimension(tokens: list[str], layer: SemanticLayer) -> str | None:
    """Match one or more trailing tokens against a dimension name or label."""

    for size in range(len(tokens), 0, -1):
        phrase = "_".join(tokens[:size])
        label = " ".join(tokens[:size])
        for name, dimension in layer.dimensions.items():
            candidates = {name, name.replace("_", " "), (dimension.label or "").lower()}
            if phrase == name or label in {_normalize(c) for c in candidates if c}:
                return name
    label = " ".join(tokens)
    haystack = f" {_normalize(label)} "
    matches: list[tuple[float, int, str]] = []
    for phrase, alias in layer.aliases.items():
        if alias.target_type != "dimension" or alias.target not in layer.dimensions:
            continue
        normalized = _normalize(phrase)
        if (
            normalized
            and f" {normalized} " in haystack
            and alias.confidence >= _AUTO_ALIAS_CONFIDENCE
        ):
            matches.append((alias.confidence, len(normalized), alias.target))
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][2]
