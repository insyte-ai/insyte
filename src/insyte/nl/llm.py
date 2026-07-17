"""Resolve a free-form question into an analysis intent via the user's local AI CLI.

Insyte ships no model of its own. When Studio (or the TUI) can't map a question with the
deterministic parser, it shells out to whichever agent CLI the user already has authenticated
— ``claude`` (Claude Code) or ``codex`` — and asks it to *translate* the question into a small
JSON command. That JSON is strictly validated against the project's real metrics and
dimensions before anything runs, so the model can never widen Insyte's safety envelope: it
picks a metric, not a query.

The model is given only metric/dimension *names* and labels plus the question. It never sees
row data, connection strings, or credentials.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from importlib import resources
from typing import TYPE_CHECKING, TypeVar

from insyte.analytics.models import TimeGrain
from insyte.nl.periods import RELATIVE_PERIODS
from insyte.semantic.catalog import SemanticCatalog
from insyte.semantic.models import SemanticLayer, StarterQuestion
from insyte.semantic.proposals import (
    DerivedMetricProposal,
    apply_metric_proposal,
    validate_metric_proposal,
)
from insyte.semantic.qualifiers import unresolved_terms
from insyte.semantic.questions import QUESTION_VOCABULARY, validate_generated_questions
from insyte.tui.intent import AnalysisMode

if TYPE_CHECKING:
    from insyte.studio.schemas import DetailedReport

logger = logging.getLogger(__name__)

_E = TypeVar("_E", bound=StrEnum)

# Default non-interactive invocations. Overridable per-backend via
# ``INSYTE_STUDIO_LLM_ARGS_CLAUDE`` / ``INSYTE_STUDIO_LLM_ARGS_CODEX`` (space-separated); the
# prompt is always appended as the final argument.
_DEFAULT_ARGS: dict[str, list[str]] = {
    "claude": ["claude", "-p", "--output-format", "text"],
    # --skip-git-repo-check lets `codex exec` run when the cwd isn't a trusted git repo.
    "codex": ["codex", "exec", "--skip-git-repo-check"],
}

_TIMEOUT_SECONDS = float(os.environ.get("INSYTE_STUDIO_LLM_TIMEOUT", "90"))
# Detailed reports are a larger generation than a one-line intent, so they get a longer budget.
_REPORT_TIMEOUT_SECONDS = float(os.environ.get("INSYTE_STUDIO_REPORT_TIMEOUT", "180"))

OUT_OF_SCOPE_MESSAGE = (
    "Insyte only answers questions about the connected business data. "
    "Ask about a metric, trend, comparison, segment, forecast, or investigation."
)
CAPABILITIES_MESSAGE = (
    "I analyze the connected business data. I can calculate metrics, show trends and "
    "breakdowns, compare periods, forecast values, and investigate why a metric changed."
)

_ANALYTICS_CUES = re.compile(
    r"\b(how many|how much|metric|data|trend|timeseries|time series|breakdown|segment|"
    r"compare|comparison|versus|change|changed|increase|decrease|drop|growth|forecast|"
    r"projected|projection|average|total|count|rate|revenue|sales|orders?|customers?|"
    r"payments?|profit|margin|performance|contribution|outlier|month|quarter|year|week|day)\b",
    re.IGNORECASE,
)
_CONTEXT_FOLLOW_UP = re.compile(
    r"^\s*(and\b|also\b|what about\b|how about\b|same\b|that\b|those\b|this\b|"
    r"why\b|how\b|compare\b|break (?:it|that) down\b|show me\b)",
    re.IGNORECASE,
)
_GREETING = re.compile(
    r"^\s*(hi|hello|hey|hi there|hello there|hey there|howdy|namaste|"
    r"good morning|good afternoon|good evening)[\s!,.?]*$",
    re.IGNORECASE,
)
_CAPABILITY_QUESTION = re.compile(
    r"^\s*(what (?:can|could|do) (?:you|u) (?:do|help with)|what (?:you|u) can do|"
    r"what do (?:you|u) do|"
    r"how can (?:you|u) help(?: me)?|help)[\s!,.?]*$",
    re.IGNORECASE,
)
_SCOPE_STOP_WORDS = frozenset(
    {"avg", "average", "count", "public", "sum", "table", "total", "value"}
)


@dataclass
class Backend:
    """A resolved local AI CLI to invoke."""

    name: str
    argv: list[str]


@dataclass
class NLResolution:
    """A runnable analysis, grounded analytics guidance, or an out-of-scope result."""

    kind: str  # "analysis" | "guidance" | "out_of_scope"
    text: str | None = None
    metric: str | None = None
    secondary_metric: str | None = None
    mode: AnalysisMode | None = None
    grain: TimeGrain | None = None
    dimension: str | None = None
    period: str | None = None
    proposal: DerivedMetricProposal | None = None


def _args_for(name: str) -> list[str]:
    override = os.environ.get(f"INSYTE_STUDIO_LLM_ARGS_{name.upper()}")
    if override:
        return override.split()
    return list(_DEFAULT_ARGS[name])


def available_backends(preference: str = "auto") -> list[Backend]:
    """Ordered list of installed local AI CLIs to try. Empty when disabled or none present.

    ``preference`` is 'auto' | 'claude' | 'codex' | 'off'; the ``INSYTE_STUDIO_LLM`` environment
    variable overrides it. For 'auto' both are returned (in order) so the caller can fall back
    past one that fails — e.g. an org-disabled Claude to a working Codex.
    """

    pref = (os.environ.get("INSYTE_STUDIO_LLM") or preference or "auto").strip().lower()
    if pref == "off":
        return []
    order = [pref] if pref in ("claude", "codex") else ["claude", "codex"]
    return [Backend(name, _args_for(name)) for name in order if shutil.which(name)]


def detect_backend(preference: str = "auto") -> Backend | None:
    """Return the first available backend, or ``None``."""

    backends = available_backends(preference)
    return backends[0] if backends else None


def resolve_starter_questions(
    layer: SemanticLayer,
    backend: Backend,
    *,
    timeout: float | None = None,
    catalog: SemanticCatalog | None = None,
) -> list[StarterQuestion]:
    """Generate concise project prompts and reject every ungrounded model response."""

    allowed_dimensions: dict[str, set[str]] = {}
    metrics = []
    for name, metric in sorted(layer.metrics.items()):
        if metric.requires_confirmation:
            continue
        capability = catalog.capability(name) if catalog else None
        metric_dimensions = list(capability.dimensions) if capability else list(layer.dimensions)
        allowed_dimensions[name] = set(metric_dimensions)
        metrics.append(
            {
                "id": name,
                "label": metric.label,
                "supports_time": bool(metric.time_column),
                "allowed_dimensions": metric_dimensions,
            }
        )
    dimensions = [
        {"id": name, "label": dimension.label or name}
        for name, dimension in sorted(layer.dimensions.items())
    ]
    prompt = (
        "Create exactly four concise starter questions for a business analytics UI. "
        "Prefer 7-8 words; never exceed 10 words. Use only the exact metric and dimension "
        "IDs supplied below. Do not invent concepts, filters, values, or periods beyond common "
        "relative periods. Include a varied useful set. A segment question requires one "
        "dimension listed in that metric's allowed_dimensions. Timeseries, forecast, and "
        "investigation require supports_time=true. "
        "Apart from metric and dimension label words, use only these connecting words: "
        f"{', '.join(sorted(QUESTION_VOCABULARY))}. "
        "Return only one JSON object: "
        '{"questions":[{"question":"... ?","metric":"exact_id",'
        '"mode":"aggregate|timeseries|segment|forecast|investigation",'
        '"dimension":"exact_id or null"}]}.\n'
        f"Metrics: {json.dumps(metrics, ensure_ascii=False)}\n"
        f"Dimensions: {json.dumps(dimensions, ensure_ascii=False)}"
    )
    out = _run(backend, prompt, timeout or _TIMEOUT_SECONDS)
    if out is None:
        return []
    objects = _all_json_objects(out)
    data = next((obj for obj in reversed(objects) if "questions" in obj), None)
    if data is None:
        logger.warning("starter_questions_no_json", extra={"backend": backend.name})
        return []
    return validate_generated_questions(
        data,
        layer,
        generated_by=backend.name,
        allowed_dimensions=allowed_dimensions,
    )


def resolve_semantic_proposals(
    layer: SemanticLayer,
    profiles: list,
    backend: Backend,
    *,
    timeout: float | None = None,
) -> list[DerivedMetricProposal]:
    """Propose confirmation-required filtered metrics from exact profiled values."""

    base_metrics = [
        {
            "id": name,
            "label": metric.label,
            "source_table": metric.source_table,
        }
        for name, metric in sorted(layer.metrics.items())
        if not metric.requires_confirmation
    ]
    safe_fields = [
        {
            "column": f"{profile.table}.{profile.column}",
            "observed_values": [value for value, _ in profile.top_values[:20]],
            "cardinality": profile.cardinality.value,
        }
        for profile in profiles
        if not profile.is_pii and profile.top_values and profile.distinct_estimate <= 50
    ]
    prompt = (
        "Propose up to 8 useful derived business metrics from exact existing base metrics and "
        "profiled field values. Each proposal must inherit one base metric and add one IN filter. "
        "Use only exact IDs, columns, and observed values below. Do not write SQL or invent a "
        "definition. Every proposal is an assumption requiring user confirmation. Prefer clear "
        "states such as completed, failed, active, positive, negative, approved, or returned only "
        "when supported by field names and observed values. Return only JSON: "
        '{"proposals":[{"name":"snake_case","label":"Short label",'
        '"base_metric":"exact_id","filter_column":"exact field",'
        '"filter_values":["exact observed value"],"aliases":["short phrase"],'
        '"assumption":"explicit business definition","confidence":0.0}]}.\n'
        f"Base metrics: {json.dumps(base_metrics, ensure_ascii=False)}\n"
        f"Safe profiled fields: {json.dumps(safe_fields, ensure_ascii=False)}"
    )
    out = _run(backend, prompt, timeout or _TIMEOUT_SECONDS)
    if out is None:
        return []
    objects = _all_json_objects(out)
    data = next((obj for obj in reversed(objects) if "proposals" in obj), None)
    if data is None or not isinstance(data.get("proposals"), list):
        logger.warning("semantic_proposals_no_json", extra={"backend": backend.name})
        return []

    accepted: list[DerivedMetricProposal] = []
    working = layer
    for raw in data["proposals"][:8]:
        if not isinstance(raw, dict):
            continue
        grounding_text = " ".join(
            [
                str(raw.get("label") or ""),
                str(raw.get("assumption") or ""),
                *[str(item) for item in raw.get("aliases", [])],
            ]
        )
        proposal = validate_metric_proposal(raw, working, profiles, question=grounding_text)
        if proposal is None:
            continue
        accepted.append(proposal)
        working = apply_metric_proposal(proposal, working)
    return accepted


def build_prompt(
    question: str,
    layer: SemanticLayer,
    *,
    now: datetime | None = None,
    history: list[tuple[str, str]] | None = None,
    context: str | None = None,
    filter_fields: list[dict[str, object]] | None = None,
) -> str:
    """Construct the translation prompt (metric/dimension names, recent turns, the question)."""

    now = now or datetime.now(UTC)
    conversation = ""
    if history:
        turns = "\n".join(f"{role}: {content}" for role, content in history[-6:])
        conversation = (
            "\nRecent conversation (oldest first) — use it to resolve follow-ups like "
            '"in that period", "what about last year", or "and by city":\n'
            f"{turns}\n"
        )
    context_block = ""
    if context:
        context_block = (
            "\nStructured context from the previous analyses — use this to resolve short "
            'follow-ups like "same period", "that metric", or "by city":\n'
            f"{context}\n"
        )
    aliases: dict[tuple[str, str], list[str]] = {}
    for phrase, alias in layer.aliases.items():
        aliases.setdefault((alias.target_type, alias.target), []).append(phrase)

    def _prompt_item(object_type: str, name: str, label: str) -> str:
        known_aliases = sorted(aliases.get((object_type, name), []))
        suffix = f"; aliases: {', '.join(known_aliases)}" if known_aliases else ""
        return f"  - {name}: {label}{suffix}"

    metrics = "\n".join(
        _prompt_item("metric", name, metric.label or name)
        for name, metric in sorted(layer.metrics.items())
    )
    dimensions = "\n".join(
        _prompt_item("dimension", name, dimension.label or name)
        for name, dimension in sorted(layer.dimensions.items())
    )
    grains = ", ".join(g.value for g in TimeGrain)
    periods = ", ".join(RELATIVE_PERIODS)
    fields = json.dumps(filter_fields or [], ensure_ascii=False)
    return (
        "You translate a business question into a JSON command for a metrics engine. "
        "Do not use any tools. Respond with ONLY a single JSON object and nothing else — "
        "no prose, no markdown code fences.\n\n"
        f"Today is {now.strftime('%Y-%m-%d')}.\n\n"
        f"Available metrics (name: label):\n{metrics or '  (none)'}\n\n"
        f"Available dimensions (name: label):\n{dimensions or '  (none)'}\n\n"
        f"Time grains: {grains}\n"
        f"Relative periods: {periods}\n\n"
        f"Safe profiled filter fields and observed values: {fields}\n\n"
        "Rules:\n"
        "- This is a data analyst, not a general-purpose assistant. If the question is not "
        "about the connected business data, its metrics, dimensions, trends, comparisons, "
        "forecasts, or investigations, return kind 'out_of_scope'. Never answer general "
        "knowledge, programming, writing, personal advice, or unrelated questions.\n"
        "- Pick the single best metric by NAME from the list above.\n"
        "- Preserve every material qualifier in the question, such as positive, failed, "
        "completed, active, premium, or a named state. Never silently drop a qualifier and "
        "run the unfiltered base metric.\n"
        "- If no listed metric implements a material qualifier, return kind 'clarification'. "
        "You may propose one derived metric by inheriting a listed base_metric and filtering "
        "one exact safe profiled field to exact observed values. Never write SQL, expressions, "
        "columns, or values outside the supplied lists. The proposal always requires user "
        "confirmation.\n"
        "- A relative time phrase is a period filter, never a new derived metric. Do not propose "
        "a metric merely to represent 'this month', 'last month', 'last 6 months', or another "
        "supported time window.\n"
        "- For products/items/units sold, prefer an additive quantity metric from transaction "
        "or order-line data when available. Do not substitute a product-catalog count or active "
        "product status for completed sales volume.\n"
        "- mode: 'segment' when the user asks to break down 'by <dimension>'; "
        "'timeseries' for a trend over time (also set grain); "
        "'compare' for this-period-vs-previous (also set grain); "
        "'forecast' when the user asks for an expected / projected / estimated / extrapolated "
        "future value (e.g. 'expected sales this year') — pick the metric to project; "
        "'opportunity' when the user asks for segments where one metric is high but another "
        "metric is low (e.g. high margin and low sales volume) — set metric to the high metric, "
        "secondary_metric to the low metric, and dimension to the segment to rank; "
        "otherwise 'aggregate'.\n"
        "- period: choose one relative period token for time-scoped questions from: "
        f"{', '.join(RELATIVE_PERIODS)}. For example, 'last month' -> last_month and "
        "'last 6 months' -> last_6_months; otherwise null / all_time.\n"
        "- If an in-scope question asks for advice that cannot run as one analysis, return "
        "'guidance' that names 2-3 concrete analyses using metric and dimension NAMES from the "
        "lists above. Do not include knowledge that is not grounded in those lists.\n\n"
        "Output schema (use exactly these keys):\n"
        '{"kind": "analysis", "metric": "<metric_name>", '
        '"secondary_metric": "<metric_name>|null", '
        '"mode": "aggregate|timeseries|segment|opportunity|compare|forecast", '
        '"grain": "day|week|month|quarter|year|null", '
        '"dimension": "<dimension_name>|null", '
        '"period": "<relative_period_token>|null"}\n'
        "OR\n"
        '{"kind":"clarification","metric":"<base_metric>",'
        '"unresolved_terms":["<exact words from question>"],'
        '"proposal":{"name":"<new_snake_case_name>","label":"<short label>",'
        '"base_metric":"<metric_name>","filter_column":"<exact supplied column>",'
        '"filter_values":["<exact observed value>"],'
        '"aliases":["<phrase using words from question/base metric>"],'
        '"assumption":"<definition requiring confirmation>","confidence":0.0}}\n'
        "OR\n"
        '{"kind": "guidance", "text": "<short analytics-only guidance>"}\n'
        "OR\n"
        '{"kind": "out_of_scope"}\n'
        f"{conversation}\n"
        f"{context_block}\n"
        f"Question: {question}"
    )


def _extract_json(text: str) -> dict | None:
    """Return the best JSON object in ``text``.

    CLIs like ``codex exec`` wrap the model's answer in banners/logs and may print several JSON
    objects (session info, etc.). We collect every balanced top-level object and prefer the last
    one carrying our schema's ``kind`` key, falling back to the last parseable object.
    """

    objects = _all_json_objects(text)
    if not objects:
        return None
    for obj in reversed(objects):
        if "kind" in obj:
            return obj
    return objects[-1]


def _all_json_objects(text: str) -> list[dict]:
    """Scan ``text`` and return every balanced top-level ``{...}`` that parses as a dict."""

    results: list[dict] = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    obj = json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    start = -1
                    continue
                if isinstance(obj, dict):
                    results.append(obj)
                start = -1
    return results


def _validate(
    data: dict,
    layer: SemanticLayer,
    *,
    profiles: list | None = None,
    question: str = "",
) -> NLResolution | None:
    """Coerce raw model JSON into a safe :class:`NLResolution`, or ``None`` if unusable."""

    kind = str(data.get("kind") or "").lower()
    if kind == "out_of_scope" or kind == "message":
        # ``message`` is the legacy open-ended response type. Treat it as out of scope so an
        # older or non-compliant model can never turn Insyte into a general-purpose chatbot.
        return NLResolution("out_of_scope")
    if kind == "guidance":
        text = str(data.get("text") or "").strip()
        if text and _mentions_semantic_object(text, layer):
            return NLResolution("guidance", text=text)
        return NLResolution("out_of_scope")

    if kind == "clarification":
        metric_name = _match_key(str(data.get("metric") or ""), layer.metrics)
        if metric_name is None:
            return None
        unresolved = [
            str(item).strip()
            for item in data.get("unresolved_terms", [])
            if str(item).strip() and str(item).strip().casefold() in question.casefold()
        ]
        if not unresolved:
            return None
        proposal = validate_metric_proposal(
            data.get("proposal"), layer, profiles or [], question=question
        )
        label = layer.metrics[metric_name].label
        terms = ", ".join(dict.fromkeys(unresolved))
        if proposal is None:
            return NLResolution(
                "clarification",
                text=(
                    f"I found {label}, but I cannot safely define {terms} from the available "
                    "schema evidence. Please specify the exact field and values that define it."
                ),
                metric=metric_name,
            )
        return NLResolution(
            "clarification",
            text=(
                f"I found {label}, but this definition needs confirmation: "
                f"{proposal.assumption} Proposed metric: {proposal.name}."
            ),
            metric=metric_name,
            proposal=proposal,
        )

    # Treat anything else as an analysis attempt; the metric must be real.
    raw_metric = str(data.get("metric") or "").strip()
    metric = _match_key(raw_metric, layer.metrics)
    if metric is None:
        return None

    secondary_metric = _match_key(str(data.get("secondary_metric") or ""), layer.metrics)
    mode = _as_enum(str(data.get("mode") or ""), AnalysisMode) or AnalysisMode.aggregate
    grain = _as_enum(str(data.get("grain") or ""), TimeGrain)
    dimension = _match_key(str(data.get("dimension") or ""), layer.dimensions)
    period = data.get("period")
    period = str(period).strip().lower() if period else None
    if period not in RELATIVE_PERIODS:
        period = None

    # Reconcile mode with the fields actually present.
    if mode is AnalysisMode.opportunity and secondary_metric and dimension:
        metric, secondary_metric = _reconcile_opportunity_metrics(
            metric, secondary_metric, dimension, layer
        )
    if mode is AnalysisMode.segment and dimension is None:
        mode = AnalysisMode.aggregate
    if mode is AnalysisMode.opportunity and (secondary_metric is None or dimension is None):
        mode = AnalysisMode.aggregate
    if mode is AnalysisMode.timeseries and grain is None:
        grain = TimeGrain.month
    if mode is AnalysisMode.compare and grain is None:
        grain = TimeGrain.month

    unresolved = (
        unresolved_terms(question, metric, layer, dimension_name=dimension) if question else []
    )
    if unresolved:
        return NLResolution(
            "clarification",
            text=(
                f"I found {layer.metrics[metric].label}, but the current metric definition "
                f"does not represent: {', '.join(unresolved)}. Please define the exact field "
                "and values for those terms."
            ),
            metric=metric,
        )

    return NLResolution(
        "analysis",
        metric=metric,
        secondary_metric=secondary_metric,
        mode=mode,
        grain=grain,
        dimension=dimension,
        period=period,
    )


def _match_key(value: str, mapping: dict) -> str | None:
    value = value.strip()
    if not value:
        return None
    if value in mapping:
        return value
    lowered = value.lower().replace(" ", "_")
    for key in mapping:
        if key.lower() == lowered:
            return key
    return None


def _reconcile_opportunity_metrics(
    primary_name: str,
    secondary_name: str,
    dimension_name: str,
    layer: SemanticLayer,
) -> tuple[str, str]:
    """Prefer opportunity metrics that share one source/grain.

    The model sees metric names and labels, but it does not understand grain as strongly as the
    SQL generator does. If it picks a high metric from one table and a low metric from another,
    look for semantically similar alternatives on a single source table. This is generic: it
    works for grocery products, library circulation, rideshare trips, or any reporting view the
    scanner turned into metrics.
    """

    primary = layer.metrics[primary_name]
    secondary = layer.metrics[secondary_name]
    if primary.source_table == secondary.source_table:
        return primary_name, secondary_name

    dimension = layer.dimensions[dimension_name]
    preferred_source = _qualify_table(dimension.table)
    source_tables = {m.source_table for m in layer.metrics.values()}
    best: tuple[int, str, str] | None = None
    for source_table in sorted(source_tables):
        primary_candidate = _best_metric_on_source(primary_name, source_table, layer)
        secondary_candidate = _best_metric_on_source(secondary_name, source_table, layer)
        if primary_candidate is None or secondary_candidate is None:
            continue
        primary_score, resolved_primary = primary_candidate
        secondary_score, resolved_secondary = secondary_candidate
        if resolved_primary == resolved_secondary:
            continue
        score = primary_score + secondary_score
        if _qualify_table(source_table) == preferred_source:
            score += 4
        if best is None or score > best[0]:
            best = (score, resolved_primary, resolved_secondary)

    if best is None or best[0] < 4:
        return primary_name, secondary_name
    return best[1], best[2]


def _best_metric_on_source(
    target_name: str, source_table: str, layer: SemanticLayer
) -> tuple[int, str] | None:
    target = layer.metrics[target_name]
    target_words = _metric_words(target_name, target.label)
    best: tuple[int, str] | None = None
    for name, metric in layer.metrics.items():
        if metric.source_table != source_table:
            continue
        score = len(target_words & _metric_words(name, metric.label))
        if score == 0:
            continue
        if metric.format == target.format:
            score += 1
        if best is None or score > best[0]:
            best = (score, name)
    return best


def _metric_words(name: str, label: str) -> set[str]:
    words = {
        word
        for word in re.split(r"[^a-z0-9]+", f"{name} {label}".lower())
        if len(word) > 2 and word not in {"sum", "avg", "total", "average", "count"}
    }
    singulars = {word[:-1] for word in words if word.endswith("s") and len(word) > 3}
    return words | singulars


def is_analytics_question(
    question: str, layer: SemanticLayer, *, has_context: bool = False
) -> bool:
    """Conservatively decide whether free-form text belongs in the analytics resolver.

    Deterministically parsed metric questions never need this gate. This protects only the
    open-ended AI fallback, where failing closed is preferable to answering unrelated prompts.
    """

    if _mentions_semantic_object(question, layer) or _ANALYTICS_CUES.search(question):
        return True
    return bool(has_context and _CONTEXT_FOLLOW_UP.search(question))


def builtin_conversation_reply(question: str) -> str | None:
    """Return a safe fixed reply for greetings and capability questions."""

    match = _GREETING.fullmatch(question)
    if match:
        greeting = match.group(1).lower()
        opening = greeting.capitalize() if greeting.startswith("good ") else "Hi"
        return (
            f"{opening}! I'm Insyte, your data analyst. Ask me about metrics, trends, "
            "comparisons, segments, forecasts, or why something changed."
        )
    if _CAPABILITY_QUESTION.fullmatch(question):
        return CAPABILITIES_MESSAGE
    return None


def _mentions_semantic_object(text: str, layer: SemanticLayer) -> bool:
    question_words = _words(text)
    if not question_words:
        return False
    semantic_words: set[str] = set()
    for name, metric in layer.metrics.items():
        semantic_words.update(_words(f"{name} {metric.label} {metric.source_table}"))
    for name, dimension in layer.dimensions.items():
        semantic_words.update(_words(f"{name} {dimension.label or ''} {dimension.source}"))
    for name, entity in layer.entities.items():
        semantic_words.update(
            _words(f"{name} {entity.table} {entity.primary_key} {entity.time_column or ''}")
        )
    semantic_words.update(_words(" ".join(layer.aliases)))
    semantic_words.difference_update(_SCOPE_STOP_WORDS)
    return bool(question_words & semantic_words)


def _words(text: str) -> set[str]:
    words = {word for word in re.split(r"[^a-z0-9]+", text.lower()) if len(word) > 2}
    words.update(word[:-1] for word in list(words) if word.endswith("s") and len(word) > 3)
    return words


def _qualify_table(table: str) -> str:
    return table if "." in table else f"public.{table}"


def _as_enum(value: str, enum: type[_E]) -> _E | None:
    value = value.strip().lower()
    if not value or value == "null":
        return None
    try:
        return enum(value)
    except ValueError:
        return None


def resolve(
    question: str,
    layer: SemanticLayer,
    backend: Backend,
    *,
    now: datetime | None = None,
    timeout: float | None = None,
    history: list[tuple[str, str]] | None = None,
    context: str | None = None,
    catalog: SemanticCatalog | None = None,
) -> NLResolution | None:
    """Run the local AI CLI to translate ``question``; return ``None`` on any failure."""

    retrieval_text = " ".join(filter(None, [question, context or ""]))
    routing_layer = layer.model_copy(deep=True)
    routing_layer.metrics = {
        name: metric
        for name, metric in routing_layer.metrics.items()
        if not metric.requires_confirmation
    }
    routing_layer.aliases = {
        phrase: alias
        for phrase, alias in routing_layer.aliases.items()
        if alias.target_type != "metric" or alias.target in routing_layer.metrics
    }
    source_catalog = catalog or SemanticCatalog(layer)
    routing_catalog = SemanticCatalog(
        routing_layer,
        profiles=source_catalog.profiles,
        relationships=source_catalog.relationships,
    )
    prompt_layer, candidates = routing_catalog.narrowed_layer(retrieval_text)
    if candidates:
        logger.info(
            "nl_candidates_selected",
            extra={
                "candidates": [
                    {"type": item.object_type, "name": item.name, "score": item.score}
                    for item in candidates
                ]
            },
        )
    prompt = build_prompt(
        question,
        prompt_layer,
        now=now,
        history=history,
        context=context,
        filter_fields=routing_catalog.safe_filter_fields(prompt_layer),
    )
    out = _run(backend, prompt, timeout or _TIMEOUT_SECONDS)
    if out is None:
        return None
    data = _extract_json(out)
    if data is None:
        logger.warning("nl_resolve_no_json", extra={"backend": backend.name})
        return None
    return _validate(
        data,
        routing_layer,
        profiles=source_catalog.profiles,
        question=question,
    )


# --------------------------------------------------------------------------------------------
# Detailed report (opt-in): the model writes analyst prose over an already-computed, grounded
# payload (see insyte.analytics.report). It never sees raw rows or credentials, and emits no SQL
# or chart data — only the JSON report described by report_skill.md.
# --------------------------------------------------------------------------------------------

_persona_cache: str | None = None

_REPORT_LIST_KEYS = frozenset(
    {
        "key_insights",
        "data_quality",
        "risks",
        "recommendations",
        "caveats",
        "evidence",
        "counter_evidence",
        "confidence_reasons",
        "next_best_questions",
        "metrics_to_track",
    }
)
_REPORT_OBJECT_KEYS = frozenset({"root_cause", "business_impact", "forecast"})


def _persona() -> str:
    """Load and cache the analyst persona bundled alongside this module."""

    global _persona_cache
    if _persona_cache is None:
        _persona_cache = (
            resources.files("insyte.nl").joinpath("report_skill.md").read_text(encoding="utf-8")
        )
    return _persona_cache


def build_report_prompt(payload: dict) -> str:
    """Wrap the analyst persona around the grounded JSON payload."""

    data = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        f"{_persona()}\n\n"
        "## Analysis payload (JSON)\n\n"
        "Everything you may reason about is below. Use only these figures — do not invent any.\n\n"
        f"{data}\n\n"
        "Return ONLY the JSON report object described above — no prose, no code fences."
    )


def resolve_report(
    payload: dict, backend: Backend, *, timeout: float | None = None
) -> DetailedReport | None:
    """Ask the local AI CLI to write a detailed report over ``payload``. ``None`` on any failure."""

    prompt = build_report_prompt(payload)
    out = _run(backend, prompt, timeout or _REPORT_TIMEOUT_SECONDS)
    if out is None:
        return None
    data = _extract_report_json(out)
    if data is None:
        logger.warning("nl_report_no_json", extra={"backend": backend.name})
        return None
    return _validate_report(data, backend.name)


def _extract_report_json(text: str) -> dict | None:
    """Pick the report object from CLI output (prefer one carrying report keys; else the last)."""

    objects = _all_json_objects(text)
    if not objects:
        return None
    for obj in reversed(objects):
        if "tl_dr" in obj or "executive_summary" in obj or "key_insights" in obj:
            return obj
    return objects[-1]


def _validate_report(data: dict, backend_name: str) -> DetailedReport | None:
    """Coerce raw model JSON into a :class:`DetailedReport`, or ``None`` if unusable."""

    from pydantic import ValidationError  # local: keep nl.llm import-light

    from insyte.studio.schemas import DetailedReport

    cleaned: dict = {}
    for key, value in data.items():
        if key in _REPORT_LIST_KEYS and not isinstance(value, list):
            continue
        if key in _REPORT_OBJECT_KEYS and not isinstance(value, dict):
            continue
        cleaned[key] = value

    try:
        report = DetailedReport.model_validate(cleaned)
    except ValidationError:
        logger.warning("nl_report_invalid", extra={"backend": backend_name})
        return None
    report.generated_by = backend_name
    # An empty shell (no summary, no insights) is not worth showing — let the result stand alone.
    if (
        not report.tl_dr.strip()
        and not report.executive_summary.strip()
        and not report.key_insights
    ):
        return None
    return report


def _run(backend: Backend, prompt: str, timeout: float) -> str | None:
    """Invoke the local AI CLI once; return its stdout, or ``None`` on spawn/timeout failure."""

    try:
        proc = subprocess.run(  # noqa: S603 - trusted local CLI, no shell
            [*backend.argv, prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warning("nl_run_timeout", extra={"backend": backend.name})
        return None
    except OSError as exc:
        logger.warning("nl_run_spawn_failed", extra={"backend": backend.name, "error": str(exc)})
        return None
    return proc.stdout or ""
