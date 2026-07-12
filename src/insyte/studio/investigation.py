"""Deterministic investigation workflow for broad Studio questions.

Investigation Mode Lite intentionally uses the existing :class:`AnalysisService` entry points.
It plans a few safe metric analyses, records what ran or was skipped, and returns a structured
timeline for Studio to render.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime

from insyte.analytics.models import AnalysisResult as DomainAnalysisResult
from insyte.analytics.models import TimeGrain
from insyte.analytics.periods import periods_for_grain
from insyte.analytics.report import MAX_REPORT_ROWS
from insyte.exceptions import InsyteError, QueryValidationError
from insyte.metadata.models import ColumnProfile
from insyte.nl.llm import Backend, resolve_report
from insyte.semantic.models import MetricFormat, SemanticLayer
from insyte.services.analysis_service import AnalysisService
from insyte.studio.schemas import (
    AnalysisResult,
    DataFreshness,
    DetailedReport,
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
    ) -> None:
        self._analysis = analysis
        self._layer = layer
        self._freshness = freshness
        self._suggestions = suggestions
        self._profiles = profiles or []

    def plan(self, question: str, intent: Intent) -> InvestigationPlan:
        metric = intent.metric
        assert metric is not None
        dimension = intent.dimension or _pick_dimension(list(self._layer.dimensions))
        metric_def = self._layer.metrics.get(metric)
        has_time = bool(metric_def and metric_def.time_column)

        steps = [
            InvestigationStep(
                id="trend",
                title="Review the metric trend",
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
                title="Compare the current period with the previous period",
                kind="comparison",
                status="pending" if has_time else "skipped",
                limitation=(
                    ""
                    if has_time
                    else "This metric has no time column, so period comparison was skipped."
                ),
            ),
            InvestigationStep(
                id="segment_breakdown",
                title="Check the top segment breakdown",
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
        return InvestigationPlan(
            question=question,
            metric=metric,
            dimension=dimension,
            period="current month vs previous month" if has_time else None,
            steps=steps,
        )

    def run(
        self,
        *,
        analysis_id: str,
        plan: InvestigationPlan,
        emit: Callable[[str, dict], str],
        detailed: bool = False,
        report_backends: list[Backend] | None = None,
    ) -> Iterator[str | AnalysisResult]:
        """Yield SSE strings while running, then yield the final Studio result."""

        metric_def = self._layer.metrics[plan.metric]
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

            step.status = "running"
            yield emit("investigation_step_started", {"step": step.model_dump(mode="json")})
            try:
                if step.kind == "trend":
                    domain = self._analysis.timeseries(plan.metric, TimeGrain.month)
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
                    current, baseline = periods_for_grain(TimeGrain.month)
                    comparison = self._analysis.compare(plan.metric, current, baseline)
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
                        domain = self._analysis.segment(plan.metric, plan.dimension, limit=10)
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
                    step.status = "completed"
                    step.key_finding = _quality_finding(self._freshness)
                    findings.append(step.key_finding)
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
                for backend in backends:
                    result.report = resolve_report(payload, backend)
                    if result.report is not None:
                        break
                if result.report is not None:
                    yield emit("report_ready", {"backend": result.report.generated_by})
                else:
                    result.report = _detailed_report(plan, findings, limitations, next_questions)
                    result.warnings.append(
                        "Detailed investigation report fell back to deterministic summary."
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


def _pick_dimension(dimensions: list[str]) -> str | None:
    if not dimensions:
        return None
    for token in _PREFERRED_DIMENSIONS:
        for dimension in dimensions:
            if token in dimension.lower():
                return dimension
    return dimensions[0]


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
    if value is None or isinstance(value, (bool, int, float, str)):
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
