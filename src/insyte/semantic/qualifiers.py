"""Detect material question terms that a resolved semantic plan failed to represent."""

from __future__ import annotations

import re

from insyte.semantic.models import SemanticLayer

_WORD = re.compile(r"[a-z0-9]+")
_ANALYSIS_WORDS_TEXT = (
    "a about across all amount analysis analyze annual annually are average be break by can "
    "change changed compare comparison count current daily data day decrease decreased decline "
    "declined did do does drop dropped expected explain for forecast from give growth has have "
    "highest how i in increase increased investigate is last least low many "
    "april august december february january july june march may me month monthly most much next "
    "november october of on over period previous projected quarter quarterly september show "
    "tell the this time to total trend trending u value versus volume vs was week weekly were "
    "what when which "
    "who why year yearly you"
)
_ANALYSIS_WORDS = frozenset(_ANALYSIS_WORDS_TEXT.split())


def unresolved_terms(
    question: str,
    metric_name: str,
    layer: SemanticLayer,
    *,
    dimension_name: str | None = None,
) -> list[str]:
    """Return significant words absent from the selected metric/dimension definition."""

    metric = layer.metrics.get(metric_name)
    if metric is None:
        return []
    represented = _words(
        " ".join(
            (
                metric_name,
                metric.label,
                metric.source_table,
                metric.expression,
                " ".join(metric.filters),
                " ".join(str(value) for values in metric.filters.values() for value in values),
            )
        )
    )
    for phrase, alias in layer.aliases.items():
        if alias.target_type == "metric" and alias.target == metric_name:
            represented |= _words(phrase)
    if dimension_name and dimension_name in layer.dimensions:
        dimension = layer.dimensions[dimension_name]
        represented |= _words(
            f"{dimension_name} {dimension.label or ''} {dimension.source}"
        )

    remaining: list[str] = []
    seen: set[str] = set()
    for raw in _WORD.findall(question.casefold()):
        if raw.isdigit():
            continue
        word = _singular(raw)
        if len(word) < 3 or word in _ANALYSIS_WORDS or word in represented or word in seen:
            continue
        seen.add(word)
        remaining.append(word)
    return remaining


def _words(value: str) -> set[str]:
    return {_singular(word) for word in _WORD.findall(value.replace("_", " ").casefold())}


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word
