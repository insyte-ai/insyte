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
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from importlib import resources
from typing import TYPE_CHECKING, TypeVar

from insyte.analytics.models import TimeGrain
from insyte.nl.periods import RELATIVE_PERIODS
from insyte.semantic.models import SemanticLayer
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


@dataclass
class Backend:
    """A resolved local AI CLI to invoke."""

    name: str
    argv: list[str]


@dataclass
class NLResolution:
    """The model's answer: either a runnable analysis intent or a plain conversational reply."""

    kind: str  # "analysis" | "message"
    text: str | None = None
    metric: str | None = None
    mode: AnalysisMode | None = None
    grain: TimeGrain | None = None
    dimension: str | None = None
    period: str | None = None


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


def build_prompt(
    question: str,
    layer: SemanticLayer,
    *,
    now: datetime | None = None,
    history: list[tuple[str, str]] | None = None,
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
    metrics = "\n".join(
        f"  - {name}: {m.label or name}" for name, m in sorted(layer.metrics.items())
    )
    dimensions = "\n".join(
        f"  - {name}: {d.label or name}" for name, d in sorted(layer.dimensions.items())
    )
    grains = ", ".join(g.value for g in TimeGrain)
    periods = ", ".join(RELATIVE_PERIODS)
    return (
        "You translate a business question into a JSON command for a metrics engine. "
        "Do not use any tools. Respond with ONLY a single JSON object and nothing else — "
        "no prose, no markdown code fences.\n\n"
        f"Today is {now.strftime('%Y-%m-%d')}.\n\n"
        f"Available metrics (name: label):\n{metrics or '  (none)'}\n\n"
        f"Available dimensions (name: label):\n{dimensions or '  (none)'}\n\n"
        f"Time grains: {grains}\n"
        f"Relative periods: {periods}\n\n"
        "Rules:\n"
        "- Pick the single best metric by NAME from the list above.\n"
        "- mode: 'segment' when the user asks to break down 'by <dimension>'; "
        "'timeseries' for a trend over time (also set grain); "
        "'compare' for this-period-vs-previous (also set grain); "
        "'forecast' when the user asks for an expected / projected / estimated / extrapolated "
        "future value (e.g. 'expected sales this year') — pick the metric to project; "
        "otherwise 'aggregate'.\n"
        "- period: choose one relative period token for time-scoped questions "
        "(e.g. 'last month' -> last_month), else null / all_time.\n"
        "- If the question asks for advice, an opinion, or a 'why'/'how' that isn't a single "
        "metric (e.g. 'how can we increase sales?', 'why did revenue drop?'), return a 'message' "
        "that is genuinely useful: briefly name 2-3 concrete analyses the user can run from the "
        "metrics and dimensions above to investigate (e.g. revenue by category, repeat customers, "
        "discount impact). For a greeting or small talk, a short friendly reply.\n\n"
        "Output schema (use exactly these keys):\n"
        '{"kind": "analysis", "metric": "<metric_name>", '
        '"mode": "aggregate|timeseries|segment|compare|forecast", '
        '"grain": "day|week|month|quarter|year|null", '
        '"dimension": "<dimension_name>|null", '
        '"period": "<relative_period_token>|null"}\n'
        "OR\n"
        '{"kind": "message", "text": "<short helpful reply>"}\n'
        f"{conversation}\n"
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


def _validate(data: dict, layer: SemanticLayer) -> NLResolution | None:
    """Coerce raw model JSON into a safe :class:`NLResolution`, or ``None`` if unusable."""

    kind = str(data.get("kind") or "").lower()
    if kind == "message":
        text = str(data.get("text") or "").strip()
        return NLResolution("message", text=text or "I can help you analyse your data.")

    # Treat anything else as an analysis attempt; the metric must be real.
    raw_metric = str(data.get("metric") or "").strip()
    metric = _match_key(raw_metric, layer.metrics)
    if metric is None:
        return None

    mode = _as_enum(str(data.get("mode") or ""), AnalysisMode) or AnalysisMode.aggregate
    grain = _as_enum(str(data.get("grain") or ""), TimeGrain)
    dimension = _match_key(str(data.get("dimension") or ""), layer.dimensions)
    period = data.get("period")
    period = str(period).strip().lower() if period else None
    if period not in RELATIVE_PERIODS:
        period = None

    # Reconcile mode with the fields actually present.
    if mode is AnalysisMode.segment and dimension is None:
        mode = AnalysisMode.aggregate
    if mode is AnalysisMode.timeseries and grain is None:
        grain = TimeGrain.month
    if mode is AnalysisMode.compare and grain is None:
        grain = TimeGrain.month

    return NLResolution(
        "analysis", metric=metric, mode=mode, grain=grain, dimension=dimension, period=period
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
) -> NLResolution | None:
    """Run the local AI CLI to translate ``question``; return ``None`` on any failure."""

    prompt = build_prompt(question, layer, now=now, history=history)
    out = _run(backend, prompt, timeout or _TIMEOUT_SECONDS)
    if out is None:
        return None
    data = _extract_json(out)
    if data is None:
        logger.warning("nl_resolve_no_json", extra={"backend": backend.name})
        return None
    return _validate(data, layer)


# --------------------------------------------------------------------------------------------
# Detailed report (opt-in): the model writes analyst prose over an already-computed, grounded
# payload (see insyte.analytics.report). It never sees raw rows or credentials, and emits no SQL
# or chart data — only the JSON report described by report_skill.md.
# --------------------------------------------------------------------------------------------

_persona_cache: str | None = None

_REPORT_LIST_KEYS = frozenset(
    {"key_insights", "data_quality", "risks", "recommendations", "caveats"}
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
        if "executive_summary" in obj or "key_insights" in obj:
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
    if not report.executive_summary.strip() and not report.key_insights:
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
