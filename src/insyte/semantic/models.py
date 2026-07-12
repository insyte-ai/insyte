"""Semantic layer models: entities, metrics and dimensions.

These mirror the ``semantic.yaml`` structure (spec §12). Auto-generation of suggested metrics
arrives in Milestone 8; in Milestone 5 the layer is authored/edited by hand and read here.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class MetricStatus(StrEnum):
    """Whether a metric is a machine suggestion or user-confirmed."""

    suggested = "suggested"
    confirmed = "confirmed"


class MetricFormat(StrEnum):
    """How a metric's values should be formatted for display."""

    number = "number"
    currency = "currency"
    percent = "percent"


class Entity(BaseModel):
    model_config = ConfigDict(extra="ignore")

    table: str
    primary_key: str = "id"
    time_column: str | None = None
    confidence: float = 1.0


class Metric(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    expression: str
    source_table: str
    filters: dict[str, list[str | int | float]] = Field(default_factory=dict)
    time_column: str | None = None
    status: MetricStatus = MetricStatus.suggested
    confidence: float = 0.5
    format: MetricFormat = MetricFormat.number


class Dimension(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str  # table.column
    type: str = "categorical"
    label: str | None = None

    @property
    def table(self) -> str:
        parts = self.source.split(".")
        return ".".join(parts[:-1]) if len(parts) >= 2 else self.source


class SemanticAlias(BaseModel):
    """A safe natural-language alias for an existing semantic object.

    Aliases are routing hints only. They never define new data, SQL, tables, columns, or values.
    """

    model_config = ConfigDict(extra="ignore")

    target: str
    target_type: str = "metric"  # metric | dimension
    confidence: float = 0.5
    evidence: list[str] = Field(default_factory=list)
    status: MetricStatus = MetricStatus.suggested


class SemanticLayer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    entities: dict[str, Entity] = Field(default_factory=dict)
    metrics: dict[str, Metric] = Field(default_factory=dict)
    dimensions: dict[str, Dimension] = Field(default_factory=dict)
    aliases: dict[str, SemanticAlias] = Field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (self.entities or self.metrics or self.dimensions or self.aliases)
