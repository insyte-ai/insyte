"""Server-Sent Events for streaming user-safe analysis progress (spec §9).

The runner emits coarse, user-visible progress events (never internal chain-of-thought) and a
final ``response_completed`` event carrying the structured :class:`AnalysisResult`. Every
analysis goes through the shared :class:`AnalysisService`, so validation, limits, PII masking
and audit logging all apply.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

from insyte.agents.report import ReportAgent
from insyte.analytics.charts import format_value
from insyte.analytics.forecast import project_current_year
from insyte.analytics.models import AnalysisKind, Period, TimeGrain
from insyte.analytics.models import AnalysisResult as DomainAnalysisResult
from insyte.analytics.periods import periods_for_grain
from insyte.config.models import AnalyticsMode, InsyteConfig
from insyte.connectors.base import DatabaseConnector
from insyte.exceptions import InsyteError, QueryValidationError
from insyte.nl.llm import (
    OUT_OF_SCOPE_MESSAGE,
    available_backends,
    builtin_conversation_reply,
    is_analytics_question,
    resolve,
)
from insyte.nl.periods import period_from_token
from insyte.nl.router import ModelRouter, ModelTask
from insyte.semantic.catalog import SemanticCatalog
from insyte.semantic.models import Metric, MetricFormat, SemanticLayer
from insyte.semantic.proposals import DerivedMetricProposal
from insyte.semantic.qualifiers import unresolved_terms
from insyte.services.analysis_service import AnalysisService
from insyte.services.schema_service import SchemaService
from insyte.studio.context import ChatContext, build_chat_context
from insyte.studio.investigation import InvestigationService, is_investigation_question
from insyte.studio.schemas import (
    AnalysisResult,
    DataFreshness,
    MetricCard,
    studio_result_blocked,
    studio_result_from_analysis,
    studio_result_from_comparison,
    studio_result_message,
)
from insyte.tui.intent import AnalysisMode, Intent, IntentKind, parse_intent

AnalysisFactory = Callable[[], tuple[AnalysisService, DatabaseConnector]]
_SOURCE = "studio"


def sse(event: str, data: dict) -> str:
    """Format one Server-Sent Event."""

    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def freshness(config: InsyteConfig, schema: SchemaService) -> DataFreshness:
    latest = schema.latest_scan() if schema.has_metadata() else None
    return DataFreshness(
        mode=config.analytics.mode.value,
        last_scan=latest.finished_at if latest else None,
    )


def stream_analysis(
    *,
    analysis_id: str,
    question: str,
    layer: SemanticLayer,
    config: InsyteConfig,
    schema: SchemaService,
    analysis_factory: AnalysisFactory,
    on_complete: Callable[[AnalysisResult, ChatContext | None], None],
    history: list[tuple[str, str]] | None = None,
    chat_context: ChatContext | None = None,
    detailed: bool = False,
    on_proposal: Callable[[DerivedMetricProposal], None] | None = None,
) -> Iterator[str]:
    """Yield SSE strings for an analysis and persist the final result via ``on_complete``."""

    yield sse("question_received", {"analysis_id": analysis_id})
    model_router = ModelRouter(config.ai, resolver=available_backends)
    intent = parse_intent(question, layer)
    if intent.kind is IntentKind.analysis and intent.metric:
        unresolved = unresolved_terms(
            question, intent.metric, layer, dimension_name=intent.dimension
        )
        if unresolved:
            # Give the grounded AI planner a chance to propose an exact profiled filter. The
            # base metric must never execute after silently dropping these terms.
            intent = Intent(IntentKind.unknown, raw=question)
    yield sse(
        "context_resolved",
        {"context": chat_context.to_dict() if chat_context is not None else None},
    )

    period: Period | None = None
    if intent.kind is not IntentKind.analysis or intent.metric is None:
        builtin_reply = builtin_conversation_reply(question)
        if builtin_reply:
            result = studio_result_message(
                analysis_id,
                builtin_reply,
                status="message",
                suggested=_suggestions(layer),
            )
            context = _final_context(
                question, result, chat_context, intent, period, detailed=detailed
            )
            result.context = context.to_dict()
            on_complete(result, context)
            yield sse("response_completed", {"result": result.model_dump(mode="json")})
            return

        has_analysis_context = bool(chat_context and chat_context.active_metric)
        if not is_analytics_question(question, layer, has_context=has_analysis_context):
            result = studio_result_message(
                analysis_id,
                OUT_OF_SCOPE_MESSAGE,
                status="out_of_scope",
                suggested=_suggestions(layer),
            )
            context = _final_context(
                question, result, chat_context, intent, period, detailed=detailed
            )
            result.context = context.to_dict()
            on_complete(result, context)
            yield sse("response_completed", {"result": result.model_dump(mode="json")})
            return

        # The deterministic parser couldn't map it — ask the user's local AI CLI to translate.
        # Try each installed CLI in turn so a failing one (e.g. an org-disabled Claude) falls
        # through to a working one (e.g. Codex).
        intent_route = model_router.route(ModelTask.intent)
        backends = intent_route.backends
        resolution = None
        yield sse(
            "model_routed",
            {
                "task": ModelTask.intent.value,
                "backends": [backend.name for backend in backends],
                "deterministic": intent_route.deterministic,
            },
        )
        if backends:
            catalog = SemanticCatalog(
                layer,
                profiles=schema.column_profiles(),
                relationships=schema.list_relationships(),
            )
            yield sse("ai_resolving", {"backend": backends[0].name})
            for backend in backends:
                resolution = resolve(
                    question,
                    layer,
                    backend,
                    history=history,
                    context=chat_context.prompt_summary() if chat_context else None,
                    catalog=catalog,
                )
                if resolution is not None:
                    break

        if resolution is None:
            result = studio_result_message(
                analysis_id,
                "I couldn't identify a metric in that question.",
                suggested=_suggestions(layer),
            )
            context = _final_context(
                question, result, chat_context, intent, period, detailed=detailed
            )
            result.context = context.to_dict()
            on_complete(result, context)
            yield sse("response_completed", {"result": result.model_dump(mode="json")})
            return

        if resolution.kind == "out_of_scope":
            result = studio_result_message(
                analysis_id,
                OUT_OF_SCOPE_MESSAGE,
                status="out_of_scope",
                suggested=_suggestions(layer),
            )
            context = _final_context(
                question, result, chat_context, intent, period, detailed=detailed
            )
            result.context = context.to_dict()
            on_complete(result, context)
            yield sse("response_completed", {"result": result.model_dump(mode="json")})
            return

        if resolution.kind == "guidance":
            result = studio_result_message(
                analysis_id,
                resolution.text or OUT_OF_SCOPE_MESSAGE,
                status="guidance",
                suggested=_suggestions(layer),
            )
            context = _final_context(
                question, result, chat_context, intent, period, detailed=detailed
            )
            result.context = context.to_dict()
            on_complete(result, context)
            yield sse("response_completed", {"result": result.model_dump(mode="json")})
            return

        if resolution.kind == "clarification":
            if resolution.proposal is not None and on_proposal is not None:
                on_proposal(resolution.proposal)
            message = resolution.text or "This business definition needs clarification."
            if resolution.proposal is not None:
                message += (
                    " Review it, then confirm with `insyte metrics approve "
                    f"{resolution.proposal.name}`."
                )
            result = studio_result_message(
                analysis_id,
                message,
                status="clarification",
                suggested=_suggestions(layer),
            )
            clarification_intent = Intent(
                IntentKind.analysis,
                metric=resolution.metric,
                mode=AnalysisMode.aggregate,
                raw=question,
            )
            context = _final_context(
                question,
                result,
                chat_context,
                clarification_intent,
                period,
                detailed=detailed,
            )
            result.context = context.to_dict()
            on_complete(result, context)
            yield sse("response_completed", {"result": result.model_dump(mode="json")})
            return

        intent = Intent(
            IntentKind.analysis,
            mode=resolution.mode or AnalysisMode.aggregate,
            metric=resolution.metric,
            secondary_metric=resolution.secondary_metric,
            grain=resolution.grain,
            dimension=resolution.dimension,
            raw=question,
        )
        period = period_from_token(resolution.period)

    selected_metric = layer.metrics.get(intent.metric) if intent.metric else None
    if selected_metric is not None and selected_metric.requires_confirmation:
        result = studio_result_message(
            analysis_id,
            (
                f"{selected_metric.label} is based on an unconfirmed assumption: "
                f"{selected_metric.assumption} Confirm it with `insyte metrics approve "
                f"{intent.metric}` before running this analysis."
            ),
            status="clarification",
            suggested=_suggestions(layer),
        )
        context = _final_context(question, result, chat_context, intent, period, detailed=detailed)
        result.context = context.to_dict()
        on_complete(result, context)
        yield sse("response_completed", {"result": result.model_dump(mode="json")})
        return

    yield sse("metric_resolved", {"metric": intent.metric})
    mode = intent.mode or AnalysisMode.aggregate
    yield sse("analysis_planned", {"mode": mode.value, "dimension": intent.dimension})

    data_freshness = freshness(config, schema)
    analysis, connector = analysis_factory()
    try:
        if is_investigation_question(question, intent):
            service = InvestigationService(
                analysis,
                layer,
                data_freshness,
                _suggestions(layer),
                profiles=schema.column_profiles(),
                relationships=schema.list_relationships(),
            )
            planner_route = model_router.route(ModelTask.planner)
            plan = service.plan(question, intent, planner_backends=planner_route.backends)
            yield sse("investigation_planned", {"plan": plan.model_dump(mode="json")})
            final_result: AnalysisResult | None = None
            for item in service.run(
                analysis_id=analysis_id,
                plan=plan,
                emit=sse,
                detailed=detailed,
                report_backends=(model_router.route(ModelTask.report).backends if detailed else []),
            ):
                if isinstance(item, str):
                    yield item
                else:
                    final_result = item
            if final_result is None:
                final_result = studio_result_message(
                    analysis_id,
                    "I couldn't complete the investigation.",
                    status="error",
                    suggested=_suggestions(layer),
                )
            context = _final_context(
                question, final_result, chat_context, intent, period, detailed=detailed
            )
            final_result.context = context.to_dict()
            on_complete(final_result, context)
            yield sse("response_completed", {"result": final_result.model_dump(mode="json")})
            return

        yield sse("sql_generated", {})
        yield sse("sql_validated", {})
        yield sse("query_started", {})
        try:
            result, report_inputs = _dispatch(
                analysis, intent, analysis_id, layer, data_freshness, period
            )
        except QueryValidationError as exc:
            result = studio_result_blocked(analysis_id, exc.violations)
            context = _final_context(
                question, result, chat_context, intent, period, detailed=detailed
            )
            result.context = context.to_dict()
            on_complete(result, context)
            yield sse("query_blocked", {"violations": exc.violations})
            yield sse("response_completed", {"result": result.model_dump(mode="json")})
            return
        except InsyteError as exc:
            result = studio_result_message(
                analysis_id, str(exc), status="error", warnings=[str(exc)]
            )
            context = _final_context(
                question, result, chat_context, intent, period, detailed=detailed
            )
            result.context = context.to_dict()
            on_complete(result, context)
            yield sse("response_completed", {"result": result.model_dump(mode="json")})
            return

        # For a detailed report, fetch a supporting monthly trend while the connector is still
        # open, so even a single-value answer gets a real chart and the AI can judge direction.
        if (
            detailed
            and report_inputs is not None
            and report_inputs.domain.kind is not AnalysisKind.timeseries
            and intent.metric is not None
        ):
            report_inputs.trend = _supporting_trend(analysis, intent.metric)
    finally:
        connector.dispose()

    yield sse("query_completed", {"rows": result.query.rows_returned if result.query else 0})
    yield sse("chart_prepared", {"charts": len(result.charts)})

    # Opt-in detailed report: analyst commentary over the completed, grounded result.
    if detailed and result.status == "completed" and report_inputs is not None:
        yield from _augment_with_report(
            question, result, report_inputs, config, schema, data_freshness
        )

    context = _final_context(question, result, chat_context, intent, period, detailed=detailed)
    result.context = context.to_dict()
    on_complete(result, context)
    yield sse("response_completed", {"result": result.model_dump(mode="json")})


def _final_context(
    question: str,
    result: AnalysisResult,
    previous: ChatContext | None,
    intent: Intent,
    period: Period | None,
    *,
    detailed: bool,
) -> ChatContext:
    return build_chat_context(
        question=question,
        result=result,
        previous=previous,
        active_metric=intent.metric,
        active_dimension=intent.dimension,
        active_period=period.label if period else previous.active_period if previous else None,
        detailed=detailed,
    )


@dataclass
class _ReportInputs:
    """The domain-level pieces a detailed report needs, kept aside during dispatch."""

    domain: DomainAnalysisResult
    fmt: MetricFormat
    metric: Metric | None
    period_label: str | None
    monthly: bool  # domain rows are month-grained → forecast bands are meaningful
    trend: DomainAnalysisResult | None = None  # supporting monthly series for detailed reports


def _dispatch(
    analysis: AnalysisService,
    intent: Intent,
    analysis_id: str,
    layer: SemanticLayer,
    data_freshness: DataFreshness,
    period: Period | None = None,
) -> tuple[AnalysisResult, _ReportInputs | None]:
    metric = intent.metric
    assert metric is not None
    fmt = layer.metrics[metric].format if metric in layer.metrics else MetricFormat.number
    suggested = _suggestions(layer)
    metric_def = layer.metrics.get(metric)
    period_label = period.label if period else None

    def inputs(domain: DomainAnalysisResult, *, monthly: bool) -> _ReportInputs:
        return _ReportInputs(domain, fmt, metric_def, period_label, monthly)

    if intent.mode is AnalysisMode.forecast:
        result, domain = _forecast(analysis, metric, analysis_id, fmt, data_freshness, suggested)
        return result, inputs(domain, monthly=True)
    if intent.mode is AnalysisMode.opportunity and intent.secondary_metric and intent.dimension:
        domain = analysis.opportunity(metric, intent.secondary_metric, intent.dimension, period)
        result = studio_result_from_analysis(analysis_id, domain, fmt, data_freshness, suggested)
        return result, inputs(domain, monthly=False)
    if intent.mode is AnalysisMode.segment and intent.dimension:
        domain = analysis.segment(metric, intent.dimension, period)
        result = studio_result_from_analysis(analysis_id, domain, fmt, data_freshness, suggested)
        return result, inputs(domain, monthly=False)
    if intent.mode is AnalysisMode.timeseries and intent.grain:
        domain = analysis.timeseries(metric, intent.grain, period)
        result = studio_result_from_analysis(analysis_id, domain, fmt, data_freshness, suggested)
        return result, inputs(domain, monthly=intent.grain is TimeGrain.month)
    if intent.mode is AnalysisMode.compare and intent.grain:
        current, baseline = periods_for_grain(intent.grain)
        comparison = analysis.compare(metric, current, baseline)
        return studio_result_from_comparison(analysis_id, comparison, fmt, data_freshness), None
    domain = analysis.aggregate(metric, period)
    result = studio_result_from_analysis(analysis_id, domain, fmt, data_freshness, suggested)
    return result, inputs(domain, monthly=False)


def _augment_with_report(
    question: str,
    result: AnalysisResult,
    inputs: _ReportInputs,
    config: InsyteConfig,
    schema: SchemaService,
    freshness: DataFreshness,
) -> Iterator[str]:
    """Generate an AI analyst report over the completed result and attach it in place."""

    # Imported lazily so the base analysis path never pays for the report machinery.
    from insyte.analytics.report import build_report_context

    if not config.ai.detailed_reports:
        return
    route = ModelRouter(config.ai, resolver=available_backends).route(ModelTask.report)
    backends = route.backends
    yield sse(
        "model_routed",
        {
            "task": ModelTask.report.value,
            "backends": [backend.name for backend in backends],
            "deterministic": route.deterministic,
        },
    )
    if not backends:
        result.warnings.append("Detailed report skipped: no local AI CLI (claude/codex) found.")
        yield sse("report_skipped", {"reason": "no_backend"})
        return

    yield sse("report_generating", {"backend": backends[0].name})

    # A supporting monthly trend gives the report a real chart (even for a scalar answer) and
    # lets the AI assess direction. Fall back to the primary series when it is already monthly.
    trend = inputs.trend
    if trend is not None and trend.rows:
        result.charts.extend(
            studio_result_from_analysis(result.analysis_id, trend, inputs.fmt, freshness, []).charts
        )
        series, points = trend, _monthly_points(trend)
    elif inputs.monthly:
        series, points = None, _monthly_points(inputs.domain)
    else:
        series, points = None, None

    payload = build_report_context(
        question=question,
        domain=inputs.domain,
        metric=inputs.metric,
        fmt=inputs.fmt,
        profiles=schema.column_profiles(),
        period_label=inputs.period_label,
        freshness_mode=freshness.mode,
        last_scan=freshness.last_scan,
        forecast_points=points,
        trend=series,
        now=datetime.now(UTC),
    )
    report, critic_review = ReportAgent().generate(payload, backends)
    if report is None:
        if critic_review is not None:
            yield sse("report_critic_completed", critic_review.model_dump(mode="json"))
            result.warnings.append(
                "Detailed report was blocked because its claims were not fully grounded."
            )
        else:
            result.warnings.append("Detailed report could not be generated this time.")
        yield sse("report_failed", {})
        return
    result.report = report
    yield sse(
        "report_critic_completed",
        {"approved": True, "unsupported_claims": [], "action": "accept"},
    )
    yield sse("report_ready", {"backend": report.generated_by})


def _supporting_trend(analysis: AnalysisService, metric: str) -> DomainAnalysisResult | None:
    """A best-effort full-history monthly series for ``metric``; ``None`` if it has no time axis."""

    try:
        return analysis.timeseries(metric, TimeGrain.month)
    except InsyteError:
        return None


def _monthly_points(domain: DomainAnalysisResult) -> list[tuple[datetime, float]] | None:
    """Extract ``(month_start, value)`` points from a month-grained timeseries domain result."""

    if domain.kind is not AnalysisKind.timeseries or len(domain.columns) < 2:
        return None
    points: list[tuple[datetime, float]] = []
    for row in domain.rows:
        if row[0] is None or row[1] is None:
            continue
        try:
            points.append((_as_datetime(row[0]), float(row[1])))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return points or None


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _forecast(
    analysis: AnalysisService,
    metric: str,
    analysis_id: str,
    fmt: MetricFormat,
    freshness: DataFreshness,
    suggested: list[str],
) -> tuple[AnalysisResult, DomainAnalysisResult]:
    """Project the current year from the metric's real monthly actuals (deterministic)."""

    domain = analysis.timeseries(metric, TimeGrain.month)
    result = studio_result_from_analysis(analysis_id, domain, fmt, freshness, suggested)

    points = [
        (_as_datetime(row[0]), float(row[1]))  # type: ignore[arg-type]
        for row in domain.rows
        if row[1] is not None
    ]
    projection = project_current_year(points, datetime.now(UTC))
    if projection is None:
        return result, domain

    projected = format_value(projection.projected_total, fmt)
    ytd = format_value(projection.ytd_actual, fmt)
    run_rate = format_value(projection.run_rate, fmt)
    result.summary = (
        f"Projected {domain.label} for {projection.year}: ~{projected}. "
        f"Based on {projection.complete_months} completed months ({ytd} so far) plus a "
        f"{run_rate}/month run-rate (average of the last {projection.basis_months}) applied to "
        f"the remaining {projection.remaining_months} months."
    )
    result.metrics = [
        MetricCard(
            label=f"{projection.year} projected",
            value=round(projection.projected_total, 2),
            format=fmt.value,
        ),
        MetricCard(label="YTD actual", value=round(projection.ytd_actual, 2), format=fmt.value),
        MetricCard(label="Monthly run-rate", value=round(projection.run_rate, 2), format=fmt.value),
    ]
    result.projection = {
        "year": projection.year,
        "method": "trailing run-rate",
        "projected_total": round(projection.projected_total, 2),
        "ytd_actual": round(projection.ytd_actual, 2),
        "run_rate": round(projection.run_rate, 2),
        "basis_months": projection.basis_months,
        "remaining_months": projection.remaining_months,
    }
    result.limitations = [
        "Estimate, not a guarantee: the remaining months are projected at the trailing "
        f"{projection.basis_months}-month average. Actual results will vary."
    ]
    return result, domain


_PREFERRED_METRICS = ("grand_total", "total_amount", "revenue", "sales", "order_count", "amount")
_PREFERRED_DIMENSIONS = ("city", "category", "payment_method", "brand", "type", "status")


def _pick(items: list[str], preferred: tuple[str, ...]) -> str:
    for token in preferred:
        for item in items:
            if token in item.lower():
                return item
    return items[0]


def _suggestions(layer: SemanticLayer) -> list[str]:
    if layer.starter_questions:
        return [item.question for item in layer.starter_questions]
    metric_names = list(layer.metrics)
    if not metric_names:
        return []
    metric = _pick(metric_names, _PREFERRED_METRICS)
    label = (layer.metrics[metric].label or metric.replace("_", " ")).lower()
    suggestions = [f"Monthly {label}"]
    dimensions = list(layer.dimensions)
    if dimensions:
        dim = _pick(dimensions, _PREFERRED_DIMENSIONS)
        dim_label = (layer.dimensions[dim].label or dim.replace("_", " ")).lower()
        suggestions.append(f"{label} by {dim_label}")
    return suggestions


def local_mode_ready(config: InsyteConfig) -> bool:
    return config.analytics.mode is AnalyticsMode.local
