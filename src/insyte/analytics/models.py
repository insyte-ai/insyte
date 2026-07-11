"""Data structures for analytical requests and results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class TimeGrain(StrEnum):
    day = "day"
    week = "week"
    month = "month"
    quarter = "quarter"
    year = "year"


class AnalysisKind(StrEnum):
    aggregate = "aggregate"
    timeseries = "timeseries"
    segment = "segment"
    comparison = "comparison"


class ChartType(StrEnum):
    line = "line"
    bar = "bar"
    horizontal_bar = "horizontal_bar"
    pie = "pie"
    scatter = "scatter"
    none = "none"


@dataclass
class ChartSpec:
    """A recommendation for how to chart a result (spec §20)."""

    type: ChartType
    title: str
    x_label: str | None = None
    y_label: str | None = None


@dataclass
class Period:
    """A named, half-open time range [start, end)."""

    label: str
    start: datetime
    end: datetime


@dataclass
class Contributor:
    """A single segment's contribution to a metric."""

    segment: str
    value: float
    share: float  # fraction of the total, 0..1


@dataclass
class AnalysisResult:
    kind: AnalysisKind
    metric: str
    label: str
    columns: list[str]
    rows: list[tuple[object, ...]]
    formatted_rows: list[list[str]]
    sql: str
    chart: ChartSpec
    summary: str
    row_count: int
    duration_ms: float
    contributors: list[Contributor] = field(default_factory=list)


@dataclass
class PeriodComparison:
    metric: str
    label: str
    current: Period
    baseline: Period
    current_value: float | None
    baseline_value: float | None
    absolute_change: float | None
    percent_change: float | None
    sql_current: str
    sql_baseline: str
    summary: str
