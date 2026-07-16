"""Typed AI-assisted investigation planning over approved operations only."""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from insyte.agents.schemas import AnalysisOperation, PlannerDecision
from insyte.nl.llm import Backend, _all_json_objects, _run
from insyte.semantic.catalog import SemanticCatalog
from insyte.semantic.models import SemanticLayer

logger = logging.getLogger(__name__)


class PlannerAgent:
    """Ask a model to select operations, then enforce catalog capabilities locally."""

    def __init__(self, layer: SemanticLayer, catalog: SemanticCatalog) -> None:
        self._layer = layer
        self._catalog = catalog

    def plan(
        self, question: str, metric: str, backends: list[Backend] | tuple[Backend, ...]
    ) -> PlannerDecision | None:
        metric_def = self._layer.metrics.get(metric)
        capability = self._catalog.capability(metric)
        if metric_def is None:
            return None
        dimensions = list(capability.dimensions) if capability else []
        prompt = (
            "Create a safe analytics investigation plan. Return only JSON with exact keys: "
            '{"metric":"exact metric","dimension":"exact dimension or null",'
            '"operations":["trend|comparison|segment|quality|report"],'
            '"rationale":"brief","confidence":"high|medium|low"}. '
            "Use only the supplied metric, dimensions, and operation names. Do not provide SQL, "
            "filters, joins, expressions, tools, or new metrics. Include quality and report. "
            f"Question: {question}\nMetric: {metric}\n"
            f"Has time column: {bool(metric_def.time_column)}\n"
            f"Allowed dimensions: {json.dumps(dimensions)}"
        )
        for backend in backends:
            raw = _run(backend, prompt, 90)
            if raw is None:
                continue
            objects = _all_json_objects(raw)
            for data in reversed(objects):
                decision = self._validate(data, metric, dimensions, bool(metric_def.time_column))
                if decision is not None:
                    decision.generated_by = backend.name
                    return decision
        return None

    @staticmethod
    def _validate(
        data: dict, metric: str, dimensions: list[str], has_time: bool
    ) -> PlannerDecision | None:
        try:
            decision = PlannerDecision.model_validate(data)
        except ValidationError:
            return None
        if decision.metric != metric or decision.dimension not in {*dimensions, None}:
            return None
        operations = list(dict.fromkeys(decision.operations))
        if (
            any(
                operation in {AnalysisOperation.trend, AnalysisOperation.comparison}
                for operation in operations
            )
            and not has_time
        ):
            return None
        if AnalysisOperation.segment in operations and decision.dimension is None:
            return None
        if (
            AnalysisOperation.quality not in operations
            or AnalysisOperation.report not in operations
        ):
            return None
        decision.operations = operations
        if decision.confidence not in {"high", "medium", "low"}:
            return None
        return decision
