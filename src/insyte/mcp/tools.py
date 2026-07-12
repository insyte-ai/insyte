"""The Insyte MCP tool service.

This holds the implementation of every MCP tool as a plain method returning a
JSON-serialisable dict. It has no dependency on the MCP transport, so it is fully
unit-testable. The MCP server (``server.py``) is a thin wrapper that exposes these methods.

Crucially, every database-touching tool goes through the same Milestone 4 executor / Milestone
5 engine as the CLI and TUI, so MCP clients cannot bypass SQL validation, permission checks,
row limits, timeouts, or audit logging — and never receive the connection URL.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from insyte.analytics.charts import recommend_chart
from insyte.analytics.engine import AnalyticsEngine
from insyte.analytics.models import AnalysisKind, TimeGrain
from insyte.analytics.periods import periods_for_grain
from insyte.config.models import InsyteConfig
from insyte.exceptions import InsyteError, QueryValidationError
from insyte.metadata.repository import MetadataRepository
from insyte.query.executor import QueryExecutor
from insyte.semantic.models import SemanticLayer
from insyte.services.analysis_service import AnalysisService
from insyte.services.history_service import HistoryService
from insyte.services.schema_service import SchemaService
from insyte.tui.intent import AnalysisMode, IntentKind, parse_intent

_SOURCE = "mcp"


@dataclass
class AnalyticsBundle:
    """A shared executor + engine (same underlying connection)."""

    executor: QueryExecutor
    engine: AnalyticsEngine


class InsyteToolService:
    """Implements the Insyte MCP tools as JSON-returning methods."""

    def __init__(
        self,
        config: InsyteConfig,
        layer: SemanticLayer,
        metadata: MetadataRepository,
        bundle_provider: Callable[[], AnalyticsBundle],
    ) -> None:
        self._config = config
        self._layer = layer
        self._metadata = metadata
        self._bundle_provider = bundle_provider
        self._bundle: AnalyticsBundle | None = None
        self._schema = SchemaService(metadata)
        self._history = HistoryService(metadata)
        self._analysis: AnalysisService | None = None

    def _get_bundle(self) -> AnalyticsBundle:
        if self._bundle is None:
            self._bundle = self._bundle_provider()
        return self._bundle

    def _get_analysis(self) -> AnalysisService:
        if self._analysis is None:
            bundle = self._get_bundle()
            self._analysis = AnalysisService(bundle.engine, bundle.executor)
        return self._analysis

    # -- schema tools (local metadata, no database connection) -------------------------------

    def get_database_summary(self) -> dict:
        summary = self._schema.database_summary()
        if not summary.scanned:
            return {"scanned": False, "message": "No metadata yet. Run 'insyte scan'."}
        return {
            "scanned": True,
            "project": self._config.project.name,
            "database": self._config.database.type.value,
            "schemas": summary.schemas,
            "table_count": len(summary.tables),
            "tables": [
                {
                    "name": t.qualified_name,
                    "kind": t.kind,
                    "category": t.category,
                    "row_estimate": t.row_estimate,
                    "columns": t.column_count,
                }
                for t in summary.tables
            ],
            "last_scan": summary.last_scan.isoformat() if summary.last_scan else None,
        }

    def search_schema(self, query: str, limit: int = 20) -> dict:
        matches = self._schema.search(query, limit)
        return {
            "query": query,
            "matches": [
                {
                    "table": m.table,
                    "kind": m.kind,
                    "category": m.category,
                    "matched_columns": m.matched_columns,
                }
                for m in matches
            ],
        }

    def describe_table(self, name: str) -> dict:
        schema, _, table = name.rpartition(".")
        detail = self._schema.get_table(schema or None, table)
        if detail is None:
            return {"error": f"Table '{name}' not found. Run 'insyte scan' or check the name."}
        return {
            "table": detail.summary.qualified_name,
            "kind": detail.summary.kind,
            "category": detail.summary.category,
            "row_estimate": detail.summary.row_estimate,
            "columns": [
                {
                    "name": c.name,
                    "type": c.data_type,
                    "nullable": c.nullable,
                    "primary_key": c.is_primary_key,
                    "unique": c.is_unique,
                }
                for c in detail.columns
            ],
            "references": [_relationship(r) for r in detail.outgoing],
            "referenced_by": [_relationship(r) for r in detail.incoming],
        }

    def list_metrics(self) -> dict:
        return {
            "metrics": [
                {
                    "name": name,
                    "label": m.label,
                    "status": m.status.value,
                    "expression": m.expression,
                    "source_table": m.source_table,
                    "format": m.format.value,
                }
                for name, m in sorted(self._layer.metrics.items())
            ],
            "dimensions": [
                {"name": name, "source": d.source, "type": d.type}
                for name, d in sorted(self._layer.dimensions.items())
            ],
        }

    def get_metric_definition(self, name: str) -> dict:
        metric = self._layer.metrics.get(name)
        if metric is None:
            return {"error": f"Metric '{name}' is not defined. See insyte_list_metrics."}
        return {
            "name": name,
            "label": metric.label,
            "expression": metric.expression,
            "source_table": metric.source_table,
            "filters": metric.filters,
            "time_column": metric.time_column,
            "status": metric.status.value,
            "format": metric.format.value,
        }

    def create_analysis_plan(self, question: str) -> dict:
        intent = parse_intent(question, self._layer)
        if intent.kind is not IntentKind.analysis or intent.metric is None:
            return {
                "question": question,
                "recognized": False,
                "message": "Could not map the question to a known metric.",
                "hint": "Call insyte_list_metrics to see available metrics.",
            }
        mode = intent.mode or AnalysisMode.aggregate
        return {
            "question": question,
            "recognized": True,
            "intent": mode.value,
            "metric": intent.metric,
            "secondary_metric": intent.secondary_metric,
            "grain": intent.grain.value if intent.grain else None,
            "dimension": intent.dimension,
            "steps": _plan_steps(intent.metric, mode, intent),
            "suggested_tool": _suggested_tool(mode),
        }

    def get_query_history(self, limit: int = 20) -> dict:
        return {
            "queries": [
                {
                    "created_at": h.created_at.isoformat() if h.created_at else None,
                    "source": h.source,
                    "status": h.status,
                    "row_count": h.row_count,
                    "duration_ms": h.duration_ms,
                    "sql": h.raw_sql,
                    "error": h.error,
                }
                for h in self._history.queries(limit)
            ]
        }

    def generate_chart_spec(
        self, kind: str, columns: list[str], row_count: int, label: str = "result"
    ) -> dict:
        try:
            analysis_kind = AnalysisKind(kind)
        except ValueError:
            return {"error": f"Unknown analysis kind '{kind}'."}
        spec = recommend_chart(analysis_kind, columns, row_count, label)
        return {
            "type": spec.type.value,
            "title": spec.title,
            "x_label": spec.x_label,
            "y_label": spec.y_label,
        }

    # -- database tools (validated, audited, read-only) --------------------------------------

    def run_safe_sql(self, sql: str) -> dict:
        try:
            result = self._get_analysis().run_sql(sql, source=_SOURCE)
        except QueryValidationError as exc:
            return {"ok": False, "blocked": True, "violations": exc.violations, "error": str(exc)}
        except InsyteError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "columns": result.columns,
            "rows": [[_cell(value) for value in row] for row in result.rows],
            "row_count": result.row_count,
            "truncated": result.truncated,
            "applied_limit": result.applied_limit,
            "sql": result.normalized_sql,
            "duration_ms": round(result.duration_ms, 1),
        }

    def segment_metric(self, metric: str, dimension: str, limit: int = 20) -> dict:
        try:
            result = self._get_analysis().segment(metric, dimension, limit=limit)
        except InsyteError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "metric": metric,
            "label": result.label,
            "columns": result.columns,
            "rows": [[_cell(value) for value in row] for row in result.rows],
            "contributors": [
                {"segment": c.segment, "value": c.value, "share": round(c.share, 4)}
                for c in result.contributors
            ],
            "summary": result.summary,
            "sql": result.sql,
        }

    def compare_periods(self, metric: str, grain: str = "month") -> dict:
        try:
            time_grain = TimeGrain(grain)
        except ValueError:
            return {"ok": False, "error": f"Unknown grain '{grain}'."}
        try:
            current, baseline = periods_for_grain(time_grain)
            comparison = self._get_analysis().compare(metric, current, baseline)
        except InsyteError as exc:
            return {"ok": False, "error": str(exc)}
        return {
            "ok": True,
            "metric": metric,
            "label": comparison.label,
            "current": {"label": comparison.current.label, "value": comparison.current_value},
            "baseline": {"label": comparison.baseline.label, "value": comparison.baseline_value},
            "absolute_change": comparison.absolute_change,
            "percent_change": comparison.percent_change,
            "summary": comparison.summary,
        }


def _relationship(rel: object) -> dict:
    return {
        "from": f"{rel.source_qualified}.{','.join(rel.source_columns)}",  # type: ignore[attr-defined]
        "to": f"{rel.target_qualified}.{','.join(rel.target_columns)}",  # type: ignore[attr-defined]
        "kind": rel.kind,  # type: ignore[attr-defined]
        "confidence": rel.confidence,  # type: ignore[attr-defined]
    }


def _plan_steps(metric: str, mode: AnalysisMode, intent: object) -> list[str]:
    if mode is AnalysisMode.opportunity:
        secondary = getattr(intent, "secondary_metric", None)
        dimension = getattr(intent, "dimension", None)
        return [
            f"Resolve primary metric '{metric}' and secondary metric '{secondary}'",
            f"Join to dimension '{dimension}' via scanned relationships",
            "Rank segments where the primary metric is high and the secondary metric is low",
        ]
    if mode is AnalysisMode.segment:
        dimension = getattr(intent, "dimension", None)
        return [
            f"Resolve metric '{metric}'",
            f"Join to dimension '{dimension}' via scanned relationships",
            "Aggregate and rank segments by contribution",
        ]
    if mode is AnalysisMode.timeseries:
        grain = getattr(intent, "grain", None)
        grain_value = grain.value if grain else "period"
        return [f"Resolve metric '{metric}'", f"Bucket by {grain_value}", "Order chronologically"]
    if mode is AnalysisMode.compare:
        return [
            f"Resolve metric '{metric}'",
            "Compute current and previous period totals",
            "Report absolute and percentage change",
        ]
    return [f"Resolve metric '{metric}'", "Aggregate to a single value"]


def _suggested_tool(mode: AnalysisMode) -> str:
    return {
        AnalysisMode.opportunity: "insyte_run_safe_sql",
        AnalysisMode.segment: "insyte_segment_metric",
        AnalysisMode.compare: "insyte_compare_periods",
        AnalysisMode.timeseries: "insyte_run_safe_sql",
        AnalysisMode.aggregate: "insyte_run_safe_sql",
    }[mode]


def _cell(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)
