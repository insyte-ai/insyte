"""Analysis application service — the single entry point for running analytics.

Wraps the Milestone 5 engine and the Milestone 4 executor so the TUI, MCP server and Studio
all run analyses (and raw SQL) through exactly the same validated, audited path. No caller
touches the executor or engine directly.
"""

from __future__ import annotations

from insyte.analytics.engine import AnalyticsEngine
from insyte.analytics.models import AnalysisResult, Period, PeriodComparison, TimeGrain
from insyte.exceptions import AnalysisError
from insyte.query.executor import QueryExecutor
from insyte.query.models import ExecutionResult


class AnalysisService:
    """Run metric analyses and validated SQL through the shared safe pipeline."""

    def __init__(self, engine: AnalyticsEngine, executor: QueryExecutor | None = None) -> None:
        self._engine = engine
        self._executor = executor

    def run_sql(self, sql: str, *, source: str) -> ExecutionResult:
        if self._executor is None:
            raise AnalysisError("SQL execution is not available in this context.")
        return self._executor.execute(sql, source=source)

    def aggregate(self, metric: str, period: Period | None = None) -> AnalysisResult:
        return self._engine.aggregate(metric, period)

    def timeseries(
        self, metric: str, grain: TimeGrain, period: Period | None = None
    ) -> AnalysisResult:
        return self._engine.timeseries(metric, grain, period)

    def segment(
        self, metric: str, dimension: str, period: Period | None = None, limit: int = 20
    ) -> AnalysisResult:
        return self._engine.segment(metric, dimension, period, limit)

    def compare(self, metric: str, current: Period, baseline: Period) -> PeriodComparison:
        return self._engine.compare(metric, current, baseline)
