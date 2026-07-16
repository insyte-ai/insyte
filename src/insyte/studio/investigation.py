"""Deterministic investigation workflow for broad Studio questions.

Investigation Mode Lite intentionally uses the existing :class:`AnalysisService` entry points.
It plans a few safe metric analyses, records what ran or was skipped, and returns a structured
timeline for Studio to render.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

from insyte.agents.analyst import AnalystAgent
from insyte.agents.planner import PlannerAgent
from insyte.agents.quality import QualityAgent
from insyte.agents.report import ReportAgent
from insyte.analytics.models import (
    AnalysisResult as DomainAnalysisResult,
)
from insyte.analytics.models import (
    Period,
    TimeGrain,
)
from insyte.analytics.periods import periods_for_grain
from insyte.analytics.report import MAX_REPORT_ROWS
from insyte.exceptions import InsyteError, QueryValidationError
from insyte.metadata.models import ColumnProfile, RelationshipInfo
from insyte.nl.llm import Backend, resolve_report
from insyte.nl.periods import period_from_token
from insyte.semantic.catalog import MetricCapability, SemanticCatalog
from insyte.semantic.models import MetricFormat, SemanticLayer
from insyte.services.analysis_service import AnalysisService
from insyte.studio.schemas import (
    AnalysisResult,
    DataFreshness,
    DetailedReport,
    InvestigationPeriod,
    InvestigationPlan,
    InvestigationResult,
    InvestigationStep,
    MetricCard,
    studio_result_from_analysis,
    studio_result_from_comparison,
)
from insyte.tui.intent import Intent

_INVESTIGATION_RE = re.compile(
    r"\b(why|what caused|reason|driver|drivers|root cause|how did|how has|opportunity|"
    r"changed|change|dropped|drop|declined|decreased|increased|spiked|fell|rose)\b",
    re.IGNORECASE,
)
_PREFERRED_DIMENSIONS = ("city", "category", "payment_method", "brand", "type", "status")
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_MONTH_RE = re.compile(
    r"\b("
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|"
    r"aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
    r")\s+(\d{4})\b",
    re.IGNORECASE,
)
_RELATIVE_PERIODS: tuple[tuple[str, str, TimeGrain, TimeGrain], ...] = (
    ("this quarter", "this_quarter", TimeGrain.quarter, TimeGrain.week),
    ("last quarter", "last_quarter", TimeGrain.quarter, TimeGrain.week),
    ("this month", "this_month", TimeGrain.month, TimeGrain.day),
    ("last month", "last_month", TimeGrain.month, TimeGrain.day),
    ("this week", "this_week", TimeGrain.week, TimeGrain.day),
    ("last week", "last_week", TimeGrain.week, TimeGrain.day),
    ("this year", "this_year", TimeGrain.year, TimeGrain.month),
    ("last year", "last_year", TimeGrain.year, TimeGrain.month),
    ("today", "today", TimeGrain.day, TimeGrain.day),
    ("yesterday", "yesterday", TimeGrain.day, TimeGrain.day),
)


def is_investigation_question(question: str, intent: Intent) -> bool:
    """Return true when a metric question should use Investigation Mode Lite."""

    return bool(intent.metric and _INVESTIGATION_RE.search(question))


class InvestigationService:
    """Plan and execute a deterministic metric investigation."""

    def __init__(
        self,
        analysis: AnalysisService,
        layer: SemanticLayer,
        freshness: DataFreshness,
        suggestions: list[str],
        profiles: list[ColumnProfile] | None = None,
        relationships: list[RelationshipInfo] | None = None,
    ) -> None:
        self._analysis = analysis
        self._layer = layer
        self._freshness = freshness
        self._suggestions = suggestions
        self._profiles = profiles or []
        self._catalog = SemanticCatalog(
            layer, profiles=self._profiles, relationships=relationships or []
        )

    def plan(
        self,
        question: str,
        intent: Intent,
        planner_backends: list[Backend] | tuple[Backend, ...] | None = None,
    ) -> InvestigationPlan:
        metric = intent.metric
        assert metric is not None
        capability = self._catalog.capability(metric)
        safe_dimensions = list(capability.dimensions) if capability else []
        dimension = intent.dimension or _pick_dimension(safe_dimensions)
        if dimension is None and not self._profiles:
            dimension = _pick_dimension(list(self._layer.dimensions))
        metric_def = self._layer.metrics.get(metric)
        has_time = bool(metric_def and metric_def.time_column)
        explicit_periods = parse_period_pair(question)
        current, baseline = explicit_periods if explicit_periods is not None else (None, None)
        comparison_grain = intent.grain or TimeGrain.month
        trend_grain = TimeGrain.month
        trend_period = None
        relative = None if explicit_periods is not None else parse_relative_period_pair(question)
        if relative is not None:
            current, baseline, comparison_grain, trend_grain = relative
            trend_period = current
        period_label = None
        if current is not None and baseline is not None:
            period_label = f"{current.label} vs {baseline.label}"
        elif has_time:
            period_label = "current month vs previous month"

        coverage_limitation = _coverage_limitation(capability, current, baseline)
        steps = [
            InvestigationStep(
                id="trend",
                title=(
                    f"Review the {trend_grain.value} trend for {current.label}"
                    if trend_period is not None and current is not None
                    else "Review the metric trend"
                ),
                kind="trend",
                status="pending" if has_time else "skipped",
                limitation=(
                    ""
                    if has_time
                    else "This metric has no time column, so trend analysis was skipped."
                ),
            ),
            InvestigationStep(
                id="current_vs_previous",
                title=(
                    f"Compare {current.label} with {baseline.label}"
                    if current and baseline
                    else "Compare the current period with the previous period"
                ),
                kind="comparison",
                status="pending" if has_time else "skipped",
                limitation=(
                    coverage_limitation
                    if has_time and coverage_limitation
                    else ""
                    if has_time
                    else "This metric has no time column, so period comparison was skipped."
                ),
            ),
            InvestigationStep(
                id="segment_breakdown",
                title=(
                    f"Check segment movement from {baseline.label} to {current.label}"
                    if current and baseline
                    else "Check the top segment breakdown"
                ),
                kind="segment",
                status="pending" if dimension else "skipped",
                limitation="" if dimension else "No dimension is available for segment breakdown.",
            ),
            InvestigationStep(
                id="data_quality",
                title="Review data freshness and quality signals",
                kind="quality",
            ),
            InvestigationStep(
                id="final_report",
                title="Summarize the investigation",
                kind="report",
            ),
        ]
        plan = InvestigationPlan(
            question=question,
            metric=metric,
            dimension=dimension,
            period=period_label,
            current_period=_period_payload(current),
            baseline_period=_period_payload(baseline),
            trend_period=_period_payload(trend_period),
            comparison_grain=comparison_grain,
            trend_grain=trend_grain,
            steps=steps,
        )
        if planner_backends:
            decision = PlannerAgent(self._layer, self._catalog).plan(
                question, metric, planner_backends
            )
            if decision is not None:
                plan.dimension = decision.dimension
                by_kind = {step.kind: step for step in plan.steps}
                plan.steps = [by_kind[operation.value] for operation in decision.operations]
                plan.generated_by = decision.generated_by
        return plan

    def run(
        self,
        *,
        analysis_id: str,
        plan: InvestigationPlan,
        emit: Callable[[str, dict], str],
        detailed: bool = False,
        report_backends: list[Backend] | tuple[Backend, ...] | None = None,
    ) -> Iterator[str | AnalysisResult]:
        """Yield SSE strings while running, then yield the final Studio result."""

        metric_def = self._layer.metrics[plan.metric]
        analyst = AnalystAgent(self._analysis)
        quality = QualityAgent(self._profiles)
        fmt = metric_def.format
        findings: list[str] = []
        limitations: list[str] = []
        cards: list[MetricCard] = []
        charts = []
        table = None
        query = None
        report_domains: list[DomainAnalysisResult] = []
        comparison_payloads: list[dict] = []

        for step in plan.steps:
            if step.status == "skipped":
                limitations.append(step.limitation)
                yield emit(
                    "investigation_step_completed",
                    {"step": step.model_dump(mode="json")},
                )
                continue

            if step.limitation and step.limitation not in limitations:
                limitations.append(step.limitation)

            step.status = "running"
            yield emit("investigation_step_started", {"step": step.model_dump(mode="json")})
            try:
                if step.kind == "trend":
                    domain = analyst.trend(
                        plan.metric,
                        plan.trend_grain,
                        _period_from_payload(plan.trend_period),
                    )
                    partial = studio_result_from_analysis(
                        f"{analysis_id}:trend", domain, fmt, self._freshness, []
                    )
                    step.status = "completed"
                    step.result_id = partial.analysis_id
                    step.key_finding = domain.summary
                    findings.append(domain.summary)
                    charts.extend(partial.charts)
                    query = query or partial.query
                    table = table or partial.table
                    report_domains.append(domain)
                elif step.kind == "comparison":
                    current, baseline = _comparison_periods(plan)
                    comparison = analyst.compare(plan.metric, current, baseline)
                    partial = studio_result_from_comparison(
                        f"{analysis_id}:comparison", comparison, fmt, self._freshness
                    )
                    step.status = "completed"
                    step.result_id = partial.analysis_id
                    step.key_finding = comparison.summary
                    findings.append(comparison.summary)
                    cards.extend(partial.metrics)
                    comparison_payloads.append(_comparison_payload(comparison))
                elif step.kind == "segment":
                    if not plan.dimension:
                        step.status = "skipped"
                        step.limitation = "No dimension is available for segment breakdown."
                        limitations.append(step.limitation)
                    else:
                        segment_current, segment_baseline = _explicit_periods(plan)
                        if segment_current is not None and segment_baseline is not None:
                            domain = analyst.segment(
                                plan.metric,
                                plan.dimension,
                                current=segment_current,
                                baseline=segment_baseline,
                                limit=10,
                            )
                        else:
                            domain = analyst.segment(plan.metric, plan.dimension, limit=10)
                        partial = studio_result_from_analysis(
                            f"{analysis_id}:segment", domain, fmt, self._freshness, []
                        )
                        step.status = "completed"
                        step.result_id = partial.analysis_id
                        step.key_finding = domain.summary
                        findings.append(domain.summary)
                        table = table or partial.table
                        query = query or partial.query
                        report_domains.append(domain)
                elif step.kind == "quality":
                    assessment = quality.assess(plan.metric, metric_def, self._freshness)
                    step.status = "completed"
                    step.key_finding = assessment.summary
                    findings.append(step.key_finding)
                    limitations.extend(
                        issue.impact
                        for issue in assessment.issues
                        if issue.severity in {"warning", "critical"}
                    )
                elif step.kind == "report":
                    step.status = "completed"
                    step.key_finding = _final_finding(plan, findings, limitations)
            except QueryValidationError as exc:
                step.status = "failed"
                step.limitation = "; ".join(exc.violations)
                limitations.append(step.limitation)
            except InsyteError as exc:
                step.status = "failed"
                step.limitation = str(exc)
                limitations.append(step.limitation)

            yield emit(
                "investigation_step_completed",
                {"step": step.model_dump(mode="json")},
            )

        summary = _final_finding(plan, findings, limitations)
        next_questions = _next_questions(plan, self._layer) or self._suggestions
        result = AnalysisResult(
            analysis_id=analysis_id,
            summary=summary,
            metrics=cards,
            charts=charts[:1],
            table=table,
            query=query,
            limitations=limitations,
            suggested_questions=next_questions,
            freshness=self._freshness,
            investigation=InvestigationResult(
                plan=plan,
                summary=summary,
                findings=findings,
                limitations=limitations,
                next_questions=next_questions,
            ),
        )
        investigation_result = result.investigation
        assert investigation_result is not None
        if detailed:
            backends = report_backends or []
            if backends:
                yield emit("report_generating", {"backend": backends[0].name})
                payload = _report_payload(
                    plan=plan,
                    layer=self._layer,
                    freshness=self._freshness,
                    profiles=self._profiles,
                    domains=report_domains,
                    comparisons=comparison_payloads,
                    findings=findings,
                    limitations=limitations,
                    next_questions=next_questions,
                )
                result.report, critic_review = ReportAgent(resolver=resolve_report).generate(
                    payload, backends
                )
                if result.report is not None:
                    investigation_result.critic_status = "approved"
                    yield emit(
                        "report_critic_completed",
                        {"approved": True, "unsupported_claims": []},
                    )
                    yield emit("report_ready", {"backend": result.report.generated_by})
                else:
                    if critic_review is not None:
                        investigation_result.critic_status = critic_review.action
                        yield emit(
                            "report_critic_completed",
                            critic_review.model_dump(mode="json"),
                        )
                    result.report = _detailed_report(plan, findings, limitations, next_questions)
                    result.warnings.append(
                        "Detailed investigation report fell back to a deterministic summary "
                        "after generation or grounding review failed."
                    )
                    yield emit("report_failed", {})
            else:
                result.report = _detailed_report(plan, findings, limitations, next_questions)
                result.warnings.append(
                    "Detailed investigation report used deterministic summary: no local AI CLI "
                    "was available."
                )
                yield emit("report_skipped", {"reason": "no_backend"})
        yield emit("investigation_report_ready", {"analysis_id": analysis_id})
        yield result


def parse_period_pair(question: str) -> tuple[Period, Period] | None:
    """Extract an explicit month-over-month comparison from a question.

    Returns ``(current, baseline)``. The parser is deliberately conservative and supports only
    month/year pairs, which are the periods Investigation Mode can compare deterministically.
    """

    matches = list(_MONTH_RE.finditer(question))
    if len(matches) < 2:
        return None
    first = _period_from_match(matches[0])
    second = _period_from_match(matches[1])
    between = question[matches[0].end() : matches[1].start()].lower()
    before = question[: matches[0].start()].lower()
    if "compared to" in between:
        return first, second
    if "versus" in between or " vs" in between:
        return _chronological_pair(first, second)
    if "from" in before or " to " in f" {between} " or "through" in between:
        return second, first
    return second, first


def parse_relative_period_pair(
    question: str, *, now: datetime | None = None
) -> tuple[Period, Period, TimeGrain, TimeGrain] | None:
    """Resolve a named relative period into a comparable current/baseline pair."""

    normalized = re.sub(r"\s+", " ", question.casefold())
    now = now or datetime.now(UTC)
    for phrase, token, comparison_grain, trend_grain in _RELATIVE_PERIODS:
        if not re.search(rf"\b{re.escape(phrase)}\b", normalized):
            continue
        current = period_from_token(token, now=now)
        if current is None:
            return None
        if token.startswith("this_") or token == "today":
            current = Period(current.label, current.start, min(now, current.end))
        span = current.end - current.start
        previous_token = {
            "this_week": "last_week",
            "this_month": "last_month",
            "this_quarter": "last_quarter",
            "this_year": "last_year",
            "today": "yesterday",
        }.get(token)
        previous = period_from_token(previous_token, now=now) if previous_token else None
        if previous is not None:
            baseline = Period(
                _baseline_label(token),
                previous.start,
                min(previous.start + span, previous.end),
            )
        else:
            baseline = Period(
                _baseline_label(token),
                current.start - span,
                current.start,
            )
        return current, baseline, comparison_grain, trend_grain
    return None


def _baseline_label(token: str) -> str:
    labels = {
        "this_week": "same elapsed period last week",
        "this_month": "same elapsed period last month",
        "this_quarter": "same elapsed period last quarter",
        "this_year": "same elapsed period last year",
        "today": "same elapsed period yesterday",
    }
    if token in labels:
        return labels[token]
    return f"period before {token.replace('_', ' ')}"


def _period_from_match(match: re.Match[str]) -> Period:
    month_name = match.group(1).lower()
    year = int(match.group(2))
    month = _MONTHS[month_name]
    start = datetime(year, month, 1, tzinfo=UTC)
    end = _add_months(start, 1)
    return Period(start.strftime("%b %Y"), start, end)


def _add_months(value: datetime, months: int) -> datetime:
    index = value.month - 1 + months
    return datetime(value.year + index // 12, index % 12 + 1, 1, tzinfo=UTC)


def _chronological_pair(first: Period, second: Period) -> tuple[Period, Period]:
    return (first, second) if first.start >= second.start else (second, first)


def _period_payload(period: Period | None) -> InvestigationPeriod | None:
    if period is None:
        return None
    return InvestigationPeriod(label=period.label, start=period.start, end=period.end)


def _period_from_payload(payload: InvestigationPeriod | None) -> Period | None:
    if payload is None:
        return None
    return Period(payload.label, payload.start, payload.end)


def _explicit_periods(plan: InvestigationPlan) -> tuple[Period | None, Period | None]:
    return _period_from_payload(plan.current_period), _period_from_payload(plan.baseline_period)


def _comparison_periods(plan: InvestigationPlan) -> tuple[Period, Period]:
    current, baseline = _explicit_periods(plan)
    if current is not None and baseline is not None:
        return current, baseline
    return periods_for_grain(plan.comparison_grain)


def _pick_dimension(dimensions: list[str]) -> str | None:
    if not dimensions:
        return None
    for token in _PREFERRED_DIMENSIONS:
        for dimension in dimensions:
            if token in dimension.lower():
                return dimension
    return dimensions[0]


def _coverage_limitation(
    capability: MetricCapability | None,
    current: Period | None,
    baseline: Period | None,
) -> str:
    """Flag explicit periods outside profiled coverage without claiming sampled bounds are exact."""

    if capability is None or (capability.data_start is None and capability.data_end is None):
        return ""
    requested = [period for period in (current, baseline) if period is not None]
    if not requested:
        return ""
    start = capability.data_start.date() if capability.data_start else None
    end = capability.data_end.date() if capability.data_end else None
    outside = any(
        (start is not None and period.end.date() < start)
        or (end is not None and period.start.date() > end)
        for period in requested
    )
    if not outside:
        return ""
    bounds = " to ".join(value.isoformat() for value in (start, end) if value is not None)
    return (
        f"The requested comparison is outside the profiled time-column coverage ({bounds}); "
        "results require a data-coverage check."
    )


def _quality_finding(freshness: DataFreshness) -> str:
    if freshness.last_scan is None:
        return f"Metadata freshness is unknown in {freshness.mode} mode."
    return (
        f"Metadata was last scanned at {freshness.last_scan.isoformat()} in {freshness.mode} mode."
    )


def _final_finding(plan: InvestigationPlan, findings: list[str], limitations: list[str]) -> str:
    metric_label = plan.metric.replace("_", " ")
    usable = [finding for finding in findings if finding]
    if not usable:
        return f"I could not complete a full investigation for {metric_label}."
    lead = usable[0]
    if limitations:
        return f"Investigation for {metric_label}: {lead} Some checks were limited."
    return f"Investigation for {metric_label}: {lead}"


def _next_questions(plan: InvestigationPlan, layer: SemanticLayer) -> list[str]:
    metric = layer.metrics.get(plan.metric)
    label = (metric.label if metric else plan.metric).lower()
    questions = [f"Show monthly {label}"]
    if plan.dimension:
        dim_label = (
            layer.dimensions[plan.dimension].label or plan.dimension.replace("_", " ")
        ).lower()
        questions.append(f"Break {label} down by {dim_label}")
    if metric and metric.format is MetricFormat.currency:
        questions.append(f"What changed most for {label}?")
    return questions


def _report_payload(
    *,
    plan: InvestigationPlan,
    layer: SemanticLayer,
    freshness: DataFreshness,
    profiles: list[ColumnProfile],
    domains: list[DomainAnalysisResult],
    comparisons: list[dict],
    findings: list[str],
    limitations: list[str],
    next_questions: list[str],
) -> dict:
    metric = layer.metrics.get(plan.metric)
    fmt = metric.format if metric else MetricFormat.number
    tables = {metric.source_table} if metric else set()
    for domain in domains:
        tables.add(layer.metrics[domain.metric].source_table)
    return {
        "question": plan.question,
        "workflow": "investigation",
        "metric": {
            "name": plan.metric,
            "label": metric.label if metric else plan.metric.replace("_", " "),
            "format": fmt.value,
            "currency_convention": "Indian (₹, lakh/crore)"
            if fmt is MetricFormat.currency
            else None,
        },
        "investigation_plan": plan.model_dump(mode="json"),
        "computed_findings": findings,
        "limitations": limitations,
        "analyses": [_domain_payload(domain, layer) for domain in domains],
        "comparisons": comparisons,
        "data_quality": _profile_payload(profiles, tables),
        "freshness": {
            "mode": freshness.mode,
            "last_scan": freshness.last_scan.isoformat() if freshness.last_scan else None,
        },
        "next_questions": next_questions,
        "instructions": (
            "Write the detailed report as a business investigation. Explain what changed, "
            "what likely drove it, what evidence supports or weakens the conclusion, and what "
            "to inspect next. Use only the computed_findings, analyses, comparisons, data_quality, "
            "and freshness fields."
        ),
    }


def _domain_payload(domain: DomainAnalysisResult, layer: SemanticLayer) -> dict:
    metric = layer.metrics.get(domain.metric)
    return {
        "kind": domain.kind.value,
        "metric": domain.metric,
        "label": domain.label,
        "source_table": metric.source_table if metric else None,
        "summary": domain.summary,
        "columns": list(domain.columns),
        "rows": [[_scalar(value) for value in row] for row in domain.rows[:MAX_REPORT_ROWS]],
        "row_count": domain.row_count,
        "truncated": domain.row_count > MAX_REPORT_ROWS,
        "contributors": [
            {
                "segment": contributor.segment,
                "value": round(contributor.value, 4),
                "share_pct": round(contributor.share * 100, 2),
            }
            for contributor in domain.contributors[:10]
        ],
    }


def _comparison_payload(comparison: object) -> dict:
    return {
        "metric": getattr(comparison, "metric", ""),
        "label": getattr(comparison, "label", ""),
        "current_period": getattr(getattr(comparison, "current", None), "label", ""),
        "baseline_period": getattr(getattr(comparison, "baseline", None), "label", ""),
        "current_value": _scalar(getattr(comparison, "current_value", None)),
        "baseline_value": _scalar(getattr(comparison, "baseline_value", None)),
        "absolute_change": _scalar(getattr(comparison, "absolute_change", None)),
        "percent_change": _scalar(getattr(comparison, "percent_change", None)),
        "summary": getattr(comparison, "summary", ""),
    }


def _profile_payload(profiles: list[ColumnProfile], tables: set[str]) -> list[dict]:
    rows = []
    for profile in profiles:
        table = f"{profile.schema}.{profile.table}"
        if tables and table not in tables:
            continue
        rows.append(
            {
                "table": table,
                "column": profile.column,
                "null_fraction": round(profile.null_fraction, 4),
                "duplicate_ratio": round(profile.duplicate_ratio, 4),
                "cardinality": profile.cardinality.value,
                "is_pii": profile.is_pii,
                "pii_type": profile.pii_type,
            }
        )
    return rows[:50]


def _scalar(value: object) -> object:
    if isinstance(value, datetime):
        normalised = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        return normalised.isoformat()
    if value is None or isinstance(value, bool | int | float | str):
        return value
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)


def _detailed_report(
    plan: InvestigationPlan,
    findings: list[str],
    limitations: list[str],
    next_questions: list[str],
) -> DetailedReport:
    metric_label = plan.metric.replace("_", " ")
    usable = [finding for finding in findings if finding]
    lead = usable[0] if usable else f"No completed findings were available for {metric_label}."
    segment = next(
        (finding for finding in usable if " by " in finding and "leads with" in finding),
        "",
    )
    comparison = next((finding for finding in usable if "not enough data" in finding.lower()), "")
    if not comparison:
        comparison = next((finding for finding in usable if "increased" in finding.lower()), "")
    if not comparison:
        comparison = next((finding for finding in usable if "decreased" in finding.lower()), "")

    evidence = usable[:4]
    confidence_reasons = [
        "Every investigation step used the existing validated analytics service.",
        "The report only summarizes computed metrics, comparisons, segment results, and metadata.",
    ]
    if limitations:
        confidence_reasons.append(
            "Some checks were limited, so treat the conclusion as directional."
        )

    return DetailedReport(
        tl_dr=lead,
        decision=(
            segment
            or comparison
            or f"Use the trend and segment timeline to decide where to inspect {metric_label} next."
        ),
        executive_summary=(
            f"I investigated {metric_label} using the available time trend, period comparison, "
            "segment breakdown, and metadata freshness checks. The strongest available signal is: "
            f"{lead}"
        ),
        evidence=evidence,
        counter_evidence=limitations,
        confidence_reasons=confidence_reasons,
        next_best_questions=next_questions,
        metrics_to_track=[metric_label],
        caveats=limitations,
        confidence_overall="medium" if limitations else "high",
        generated_by="Insyte deterministic investigation",
    )
