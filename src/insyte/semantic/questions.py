"""Validate concise AI-generated starter questions against the semantic layer."""

from __future__ import annotations

import re

from insyte.semantic.models import SemanticLayer, StarterQuestion

MAX_STARTER_QUESTIONS = 4
MAX_QUESTION_WORDS = 10
_MODES = frozenset({"aggregate", "timeseries", "segment", "forecast", "investigation"})
_WORD = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)?")
_QUESTION_WORDS = (
    "what which how who where when why much many was were is are did does do has have "
    "show compare changed change changing trend trending monthly weekly daily yearly last "
    "this previous current month week quarter year expected forecast projected projection "
    "by across over time most least top highest lowest contributed contributes contributing "
    "drives drove driving grew growth dropped declined increased decreased perform performing "
    "performance investigate explain versus vs and from to in for of the a an"
)
QUESTION_VOCABULARY = frozenset(_QUESTION_WORDS.split())


def word_count(text: str) -> int:
    return len(_WORD.findall(text))


def validate_generated_questions(
    data: object,
    layer: SemanticLayer,
    *,
    generated_by: str,
    allowed_dimensions: dict[str, set[str]] | None = None,
) -> list[StarterQuestion]:
    """Accept only concise questions tied to exact known metric and dimension IDs."""

    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        return []

    accepted: list[StarterQuestion] = []
    seen: set[str] = set()
    for raw in data["questions"]:
        if not isinstance(raw, dict):
            continue
        question = " ".join(str(raw.get("question") or "").strip().split())
        metric_name = str(raw.get("metric") or "").strip()
        dimension_name = str(raw.get("dimension") or "").strip() or None
        mode = str(raw.get("mode") or "").strip().lower()
        metric = layer.metrics.get(metric_name)
        dimension = layer.dimensions.get(dimension_name) if dimension_name else None

        if (
            not question.endswith("?")
            or not 3 <= word_count(question) <= MAX_QUESTION_WORDS
            or metric is None
            or metric.requires_confirmation
            or mode not in _MODES
            or (dimension_name is not None and dimension is None)
            or (mode == "segment" and dimension is None)
            or (mode != "segment" and dimension is not None)
            or (
                mode == "segment"
                and allowed_dimensions is not None
                and dimension_name not in allowed_dimensions.get(metric_name, set())
            )
            or (mode in {"timeseries", "forecast", "investigation"} and not metric.time_column)
        ):
            continue

        known_words = _semantic_words(metric_name, metric.label)
        if dimension_name and dimension:
            known_words |= _semantic_words(
                dimension_name, dimension.label or dimension_name, dimension.source
            )
        for phrase, alias in layer.aliases.items():
            if alias.target == metric_name or alias.target == dimension_name:
                known_words |= _semantic_words(phrase)

        words = {word.lower() for word in _WORD.findall(question)}
        if not words.intersection(_semantic_words(metric_name, metric.label)):
            continue
        if (
            dimension_name
            and dimension is not None
            and not words.intersection(
                _semantic_words(dimension_name, dimension.label or dimension_name)
            )
        ):
            continue
        if words - known_words - QUESTION_VOCABULARY:
            continue

        key = question.casefold()
        if key in seen:
            continue
        seen.add(key)
        accepted.append(
            StarterQuestion(
                question=question,
                metric=metric_name,
                mode=mode,
                dimension=dimension_name,
                generated_by=generated_by,
            )
        )
        if len(accepted) == MAX_STARTER_QUESTIONS:
            break
    return accepted


def _semantic_words(*values: str) -> set[str]:
    return {word.lower() for value in values for word in _WORD.findall(value.replace("_", " "))}
