"""Chat controller: turn a line of input into a structured response.

This holds all of the chat logic and has no dependency on Textual, so it can be unit-tested
directly. The Textual app is a thin view that calls :meth:`ChatController.run` and renders the
returned :class:`Response`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from insyte.analytics.charts import format_value
from insyte.analytics.engine import AnalyticsEngine
from insyte.analytics.forecast import project_current_year
from insyte.analytics.models import AnalysisResult, Period, PeriodComparison, TimeGrain
from insyte.analytics.periods import periods_for_grain
from insyte.exceptions import InsyteError
from insyte.metadata.repository import MetadataRepository
from insyte.nl.periods import period_from_token
from insyte.semantic.models import MetricFormat, SemanticLayer
from insyte.services.analysis_service import AnalysisService
from insyte.services.history_service import HistoryService
from insyte.services.schema_service import SchemaService
from insyte.tui.intent import AnalysisMode, Intent, IntentKind, parse_intent

HELP_TEXT = """[b]Insyte chat[/b] — ask about your database.

[b]Try:[/b]
  weekly completed revenue        time series by week
  completed revenue by city       segment by a dimension
  payment failure rate            a single value
  compare completed revenue       this period vs last

[b]Commands:[/b]
  /metrics   list metrics & dimensions
  /schema    list scanned tables
  /table T   describe a table
  /history   recent queries
  /clear     clear the conversation
  /help      this help    /quit  exit"""


class ResponseKind(StrEnum):
    message = "message"
    analysis = "analysis"
    comparison = "comparison"
    table = "table"
    clear = "clear"
    quit = "quit"


@dataclass
class Response:
    kind: ResponseKind
    text: str = ""
    level: str = "info"  # info | error | success
    analysis: AnalysisResult | None = None
    comparison: PeriodComparison | None = None
    title: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)

    @classmethod
    def message(cls, text: str, level: str = "info") -> Response:
        return cls(ResponseKind.message, text=text, level=level)


class ChatController:
    """Dispatches parsed intents to the analytics engine and metadata repository."""

    def __init__(
        self,
        layer: SemanticLayer,
        metadata: MetadataRepository,
        engine_provider: Callable[[], AnalyticsEngine],
    ) -> None:
        self._layer = layer
        self._metadata = metadata
        self._engine_provider = engine_provider
        self._engine: AnalyticsEngine | None = None
        self._schema_service = SchemaService(metadata)
        self._history_service = HistoryService(metadata)
        self._analysis_service: AnalysisService | None = None

    def run(self, text: str) -> Response:
        intent = parse_intent(text, self._layer)
        # Free-form question the deterministic parser couldn't map → ask the local AI CLI,
        # exactly like Studio. (Unknown *slash* commands keep their argument and skip this.)
        if intent.kind is IntentKind.unknown and not intent.argument:
            ai_response = self._resolve_with_ai(text)
            if ai_response is not None:
                return ai_response
        handler = {
            IntentKind.help: lambda i: Response.message(HELP_TEXT),
            IntentKind.clear: lambda i: Response(ResponseKind.clear),
            IntentKind.quit: lambda i: Response(ResponseKind.quit),
            IntentKind.metrics: lambda i: self._metrics(),
            IntentKind.schema: lambda i: self._schema(),
            IntentKind.table: self._table,
            IntentKind.history: lambda i: self._history(),
            IntentKind.analysis: self._analysis,
            IntentKind.unknown: self._unknown,
        }[intent.kind]
        return handler(intent)

    def _resolve_with_ai(self, text: str) -> Response | None:
        """Translate a free-form question via the user's Claude/Codex CLI. None → no backend."""

        # Imported lazily: insyte.nl.llm imports tui.intent, so a top-level import here would
        # form a circular import through the tui package __init__.
        from insyte.nl.llm import available_backends, resolve

        backends = available_backends("auto")
        if not backends:
            return None
        resolution = None
        for backend in backends:
            resolution = resolve(text, self._layer, backend)
            if resolution is not None:
                break
        if resolution is None:
            return None
        if resolution.kind == "message":
            return Response.message(resolution.text or "I can help you analyse your data.")
        intent = Intent(
            IntentKind.analysis,
            mode=resolution.mode or AnalysisMode.aggregate,
            metric=resolution.metric,
            grain=resolution.grain,
            dimension=resolution.dimension,
            raw=text,
        )
        try:
            return self._dispatch_analysis(
                self._get_analysis(), intent, period_from_token(resolution.period)
            )
        except InsyteError as exc:
            return Response.message(str(exc), "error")

    # -- non-analysis handlers ---------------------------------------------------------------

    def _metrics(self) -> Response:
        if not self._layer.metrics:
            return Response.message("No metrics defined. Edit semantic.yaml to add some.", "error")
        rows = [
            [name, m.label, m.status.value, m.expression]
            for name, m in sorted(self._layer.metrics.items())
        ]
        rows += [
            [name, d.label or "", "dimension", d.source]
            for name, d in sorted(self._layer.dimensions.items())
        ]
        return Response(
            ResponseKind.table,
            title="Metrics & dimensions",
            columns=["Name", "Label", "Kind", "Definition"],
            rows=rows,
        )

    def _schema(self) -> Response:
        if not self._schema_service.has_metadata():
            return Response.message("No schema metadata yet. Run 'insyte scan' first.", "error")
        tables = self._schema_service.list_tables()
        rows = [
            [t.qualified_name, t.kind, _rows(t.row_estimate), str(t.column_count), t.category]
            for t in tables
        ]
        return Response(
            ResponseKind.table,
            title="Schema",
            columns=["Table", "Kind", "Rows", "Cols", "Category"],
            rows=rows,
        )

    def _table(self, intent: Intent) -> Response:
        if not intent.argument:
            return Response.message("Usage: /table <name>", "error")
        schema, _, name = intent.argument.rpartition(".")
        detail = self._schema_service.get_table(schema or None, name)
        if detail is None:
            return Response.message(f"Table '{intent.argument}' not found.", "error")
        rows = [
            [
                c.name,
                c.data_type,
                "" if c.nullable else "not null",
                "PK" if c.is_primary_key else "",
            ]
            for c in detail.columns
        ]
        return Response(
            ResponseKind.table,
            title=f"{detail.summary.qualified_name} ({detail.summary.category})",
            columns=["Column", "Type", "Null", "Key"],
            rows=rows,
        )

    def _history(self) -> Response:
        history = self._history_service.queries(20)
        if not history:
            return Response.message("No query history yet.")
        rows = [[_when(h.created_at), h.status, h.source, _short(h.raw_sql)] for h in history]
        return Response(
            ResponseKind.table,
            title="Query history",
            columns=["When", "Status", "Source", "SQL"],
            rows=rows,
        )

    def _unknown(self, intent: Intent) -> Response:
        if intent.argument:
            return Response.message(f"Unknown command '/{intent.argument}'. Try /help.", "error")
        return Response.message(
            "I couldn't identify a metric in that. Try /metrics, or /help for examples.",
            "error",
        )

    # -- analysis ----------------------------------------------------------------------------

    def _analysis(self, intent: Intent) -> Response:
        assert intent.metric is not None
        try:
            analysis = self._get_analysis()
            return self._dispatch_analysis(analysis, intent)
        except InsyteError as exc:
            return Response.message(str(exc), "error")

    def _dispatch_analysis(
        self, analysis: AnalysisService, intent: Intent, period: Period | None = None
    ) -> Response:
        metric = intent.metric
        assert metric is not None
        if intent.mode is AnalysisMode.forecast:
            return self._forecast(analysis, metric)
        if intent.mode is AnalysisMode.segment and intent.dimension:
            return Response(
                ResponseKind.analysis, analysis=analysis.segment(metric, intent.dimension, period)
            )
        if intent.mode is AnalysisMode.timeseries and intent.grain:
            return Response(
                ResponseKind.analysis, analysis=analysis.timeseries(metric, intent.grain, period)
            )
        if intent.mode is AnalysisMode.compare and intent.grain:
            current, baseline = periods_for_grain(intent.grain)
            return Response(
                ResponseKind.comparison, comparison=analysis.compare(metric, current, baseline)
            )
        return Response(ResponseKind.analysis, analysis=analysis.aggregate(metric, period))

    def _forecast(self, analysis: AnalysisService, metric: str) -> Response:
        domain = analysis.timeseries(metric, TimeGrain.month)
        fmt = (
            self._layer.metrics[metric].format
            if metric in self._layer.metrics
            else MetricFormat.number
        )
        points = [
            (_as_datetime(row[0]), float(row[1]))  # type: ignore[arg-type]
            for row in domain.rows
            if row[1] is not None
        ]
        projection = project_current_year(points, datetime.now(UTC))
        if projection is None:
            return Response(ResponseKind.analysis, analysis=domain)
        text = (
            f"Projected {domain.label} for {projection.year}: "
            f"~{format_value(projection.projected_total, fmt)}. Based on "
            f"{projection.complete_months} completed months "
            f"({format_value(projection.ytd_actual, fmt)}) plus a "
            f"{format_value(projection.run_rate, fmt)}/month run-rate. "
            "Estimate, not a guarantee."
        )
        return Response.message(text)

    def _get_analysis(self) -> AnalysisService:
        if self._analysis_service is None:
            if self._engine is None:
                self._engine = self._engine_provider()
            self._analysis_service = AnalysisService(self._engine)
        return self._analysis_service


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _rows(value: int | None) -> str:
    if value is None or value < 0:
        return "—"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def _short(sql: str, length: int = 60) -> str:
    collapsed = " ".join(sql.split())
    return collapsed if len(collapsed) <= length else collapsed[: length - 1] + "…"


def _when(value: object) -> str:
    if value is None:
        return ""
    return value.astimezone().strftime("%H:%M:%S")  # type: ignore[attr-defined]
