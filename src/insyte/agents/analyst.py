"""Approved analytics-service calls available to an investigation."""

from __future__ import annotations

from insyte.analytics.models import AnalysisResult, Period, PeriodComparison, TimeGrain
from insyte.services.analysis_service import AnalysisService


class AnalystAgent:
    """Narrow facade over :class:`AnalysisService`; it cannot execute arbitrary SQL."""

    def __init__(self, analysis: AnalysisService) -> None:
        self._analysis = analysis

    def trend(self, metric: str, grain: TimeGrain, period: Period | None = None) -> AnalysisResult:
        return self._analysis.timeseries(metric, grain, period)

    def compare(self, metric: str, current: Period, baseline: Period) -> PeriodComparison:
        return self._analysis.compare(metric, current, baseline)

    def segment(
        self,
        metric: str,
        dimension: str,
        *,
        current: Period | None = None,
        baseline: Period | None = None,
        limit: int = 10,
    ) -> AnalysisResult:
        if current is not None and baseline is not None:
            return self._analysis.segment_compare(metric, dimension, current, baseline, limit=limit)
        return self._analysis.segment(metric, dimension, limit=limit)
