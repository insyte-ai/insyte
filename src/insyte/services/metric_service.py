"""Semantic-metric application service — shared by the CLI, MCP, and Studio."""

from __future__ import annotations

from insyte.exceptions import MetricNotFoundError
from insyte.semantic.models import Dimension, Metric, MetricStatus, SemanticLayer
from insyte.semantic.repository import SemanticRepository


class MetricService:
    """Read and update the semantic layer's metrics and dimensions."""

    def __init__(self, repository: SemanticRepository) -> None:
        self._repository = repository

    def layer(self) -> SemanticLayer:
        return self._repository.load()

    def metrics(self) -> dict[str, Metric]:
        return self.layer().metrics

    def dimensions(self) -> dict[str, Dimension]:
        return self.layer().dimensions

    def get(self, name: str) -> Metric | None:
        return self.layer().metrics.get(name)

    def approve(self, name: str) -> Metric:
        """Mark a metric confirmed and persist. Raises if the metric is unknown."""

        layer = self._repository.load()
        metric = layer.metrics.get(name)
        if metric is None:
            raise MetricNotFoundError(name)
        metric.status = MetricStatus.confirmed
        self._repository.save(layer)
        return metric
