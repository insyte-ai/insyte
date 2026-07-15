"""Structured request/response models for the Studio API (spec §10).

Studio never returns bare markdown: every analysis is a typed :class:`AnalysisResult` so the
frontend can render overview, chart, data, SQL and method views consistently.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from insyte.analytics.models import AnalysisResult as DomainAnalysisResult
from insyte.analytics.models import ChartType, PeriodComparison
from insyte.semantic.models import MetricFormat

# --------------------------------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------------------------------


class ConversationCreate(BaseModel):
    title: str = "New analysis"


class TitleUpdate(BaseModel):
    title: str


class MessageRequest(BaseModel):
    content: str
    active_metric: str | None = None
    active_data_chain: str | None = None
    detailed: bool = False  # opt-in: generate an AI analyst report over the result


# --------------------------------------------------------------------------------------------
# Result models
# --------------------------------------------------------------------------------------------


class MetricCard(BaseModel):
    label: str
    value: float | None
    format: str = "number"
    currency: str | None = None
    change_percent: float | None = None


class Contributor(BaseModel):
    label: str
    absolute_change: float | None = None
    contribution_percent: float | None = None


class ChartSeries(BaseModel):
    key: str
    label: str


class ChartSpec(BaseModel):
    type: str
    title: str
    x_key: str | None = None
    series: list[ChartSeries] = Field(default_factory=list)
    data: list[dict] = Field(default_factory=list)


class DataTableResult(BaseModel):
    columns: list[str]
    rows: list[list[object]]
    row_count: int
    truncated: bool = False


class QueryDetails(BaseModel):
    sql: str
    duration_ms: float
    rows_returned: int
    validation_status: str
    applied_limit: int | None = None


class DataFreshness(BaseModel):
    mode: str
    last_scan: datetime | None = None


# --------------------------------------------------------------------------------------------
# Detailed report (opt-in): AI-written analyst commentary over the already-computed result.
# The model supplies prose only; every number and chart is produced deterministically by Insyte.
# Fields mirror src/insyte/nl/report_skill.md. All optional so a partial model reply still
# validates and degrades gracefully.
# --------------------------------------------------------------------------------------------


class KeyInsight(BaseModel):
    title: str = ""
    detail: str = ""
    evidence: str = ""
    confidence: str = "medium"  # high | medium | low
    limitations: str = ""
    alternative_explanation: str = ""


class DataQualityFlag(BaseModel):
    issue: str = ""
    severity: str = "info"  # info | warning | critical
    affected: str = ""
    impact: str = ""


class RootCause(BaseModel):
    what_changed: str = ""
    when: str = ""
    dimension: str = ""
    likely_cause: str = ""
    confidence: str = "medium"
    evidence: str = ""


class BusinessImpact(BaseModel):
    narrative: str = ""
    financial_note: str = ""


class ReportForecast(BaseModel):
    expected: str = ""
    best_case: str = ""
    worst_case: str = ""
    assumptions: str = ""
    method: str = ""


class RiskItem(BaseModel):
    risk: str = ""
    likelihood: str = "medium"
    mitigation: str = ""


class Recommendation(BaseModel):
    action: str = ""
    horizon: str = "short"  # immediate | short | long
    priority: str = "medium"  # high | medium | low
    expected_impact: str = ""
    est_roi: str = ""


class DetailedReport(BaseModel):
    tl_dr: str = ""
    decision: str = ""
    executive_summary: str = ""
    evidence: list[str] = Field(default_factory=list)
    counter_evidence: list[str] = Field(default_factory=list)
    confidence_reasons: list[str] = Field(default_factory=list)
    key_insights: list[KeyInsight] = Field(default_factory=list)
    data_quality: list[DataQualityFlag] = Field(default_factory=list)
    root_cause: RootCause | None = None
    business_impact: BusinessImpact | None = None
    forecast: ReportForecast | None = None
    risks: list[RiskItem] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    next_best_questions: list[str] = Field(default_factory=list)
    metrics_to_track: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    confidence_overall: str = "medium"
    generated_by: str = ""  # backend name, e.g. "codex"


class InvestigationStep(BaseModel):
    id: str
    title: str
    kind: str
    status: str = "pending"  # pending | running | completed | skipped | failed
    key_finding: str = ""
    result_id: str | None = None
    limitation: str = ""


class InvestigationPeriod(BaseModel):
    label: str
    start: datetime
    end: datetime


class InvestigationPlan(BaseModel):
    question: str
    metric: str
    dimension: str | None = None
    period: str | None = None
    current_period: InvestigationPeriod | None = None
    baseline_period: InvestigationPeriod | None = None
    steps: list[InvestigationStep] = Field(default_factory=list)


class InvestigationResult(BaseModel):
    plan: InvestigationPlan
    status: str = "completed"
    summary: str = ""
    findings: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    next_questions: list[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    analysis_id: str
    status: str = "completed"  # completed | message | guidance | blocked | error | out_of_scope
    summary: str
    narrative: str = ""
    metrics: list[MetricCard] = Field(default_factory=list)
    contributors: list[Contributor] = Field(default_factory=list)
    charts: list[ChartSpec] = Field(default_factory=list)
    table: DataTableResult | None = None
    query: QueryDetails | None = None
    projection: dict | None = None
    confidence: float | None = None
    limitations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)
    freshness: DataFreshness | None = None
    report: DetailedReport | None = None
    investigation: InvestigationResult | None = None
    context: dict | None = None


# --------------------------------------------------------------------------------------------
# Converters: domain results → Studio AnalysisResult
# --------------------------------------------------------------------------------------------


def _cell(value: object) -> object:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _chart(domain: DomainAnalysisResult) -> list[ChartSpec]:
    if domain.chart.type is ChartType.none or not domain.rows or len(domain.columns) < 2:
        return []
    columns = domain.columns
    data = [{columns[i]: _cell(v) for i, v in enumerate(row)} for row in domain.rows]
    return [
        ChartSpec(
            type=domain.chart.type.value,
            title=domain.chart.title,
            x_key=columns[0],
            series=[ChartSeries(key=columns[1], label=domain.label)],
            data=data,
        )
    ]


def _headline_metric(domain: DomainAnalysisResult, fmt: MetricFormat) -> list[MetricCard]:
    if not domain.rows:
        return []
    value_index = 0 if domain.kind.value == "aggregate" else 1
    if value_index >= len(domain.rows[0]):
        return []
    value = _as_float(domain.rows[-1][value_index])
    return [MetricCard(label=domain.label, value=value, format=fmt.value)]


def studio_result_from_analysis(
    analysis_id: str,
    domain: DomainAnalysisResult,
    metric_format: MetricFormat,
    freshness: DataFreshness,
    suggested: list[str],
) -> AnalysisResult:
    contributors = [
        Contributor(
            label=c.segment,
            absolute_change=round(c.value, 4),
            contribution_percent=round(c.share * 100, 2),
        )
        for c in domain.contributors
    ]
    table = DataTableResult(
        columns=domain.columns,
        rows=[[_cell(v) for v in row] for row in domain.rows],
        row_count=domain.row_count,
    )
    query = QueryDetails(
        sql=domain.sql,
        duration_ms=round(domain.duration_ms, 1),
        rows_returned=domain.row_count,
        validation_status="approved",
    )
    return AnalysisResult(
        analysis_id=analysis_id,
        summary=domain.summary,
        metrics=_headline_metric(domain, metric_format),
        contributors=contributors,
        charts=_chart(domain),
        table=table,
        query=query,
        freshness=freshness,
        suggested_questions=suggested,
    )


def studio_result_from_comparison(
    analysis_id: str,
    comparison: PeriodComparison,
    metric_format: MetricFormat,
    freshness: DataFreshness,
) -> AnalysisResult:
    metrics = [
        MetricCard(
            label=f"{comparison.label} ({comparison.current.label})",
            value=comparison.current_value,
            format=metric_format.value,
            change_percent=(
                round(comparison.percent_change, 2)
                if comparison.percent_change is not None
                else None
            ),
        ),
        MetricCard(
            label=f"{comparison.label} ({comparison.baseline.label})",
            value=comparison.baseline_value,
            format=metric_format.value,
        ),
    ]
    return AnalysisResult(
        analysis_id=analysis_id,
        summary=comparison.summary,
        metrics=metrics,
        freshness=freshness,
    )


def studio_result_blocked(analysis_id: str, violations: list[str]) -> AnalysisResult:
    return AnalysisResult(
        analysis_id=analysis_id,
        status="blocked",
        summary="Query blocked by Insyte. No query was sent to the database.",
        warnings=violations,
    )


def studio_result_message(
    analysis_id: str,
    summary: str,
    *,
    status: str = "unrecognized",
    warnings: list[str] | None = None,
    suggested: list[str] | None = None,
) -> AnalysisResult:
    return AnalysisResult(
        analysis_id=analysis_id,
        status=status,
        summary=summary,
        warnings=warnings or [],
        suggested_questions=suggested or [],
    )
