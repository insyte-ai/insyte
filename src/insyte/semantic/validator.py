"""Validate a semantic layer against scanned schema metadata (`insyte semantic validate`)."""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from insyte.metadata.repository import MetadataRepository
from insyte.semantic.models import Metric, SemanticLayer


@dataclass
class SemanticIssue:
    level: str  # error | warning
    target: str
    message: str


@dataclass
class SchemaIndex:
    """Lookup structures for validating references against scanned schema."""

    tables: set[str] = field(default_factory=set)  # "schema.table"
    columns_by_qualified: dict[str, set[str]] = field(
        default_factory=dict
    )  # "schema.table" -> cols
    columns_by_table: dict[str, set[str]] = field(default_factory=dict)  # "table" -> cols (merged)

    @classmethod
    def from_repository(cls, metadata: MetadataRepository) -> SchemaIndex:
        index = cls()
        for summary in metadata.list_tables():
            detail = metadata.get_table(summary.schema, summary.name)
            columns = {c.name for c in detail.columns} if detail else set()
            index.tables.add(summary.qualified_name)
            index.columns_by_qualified[summary.qualified_name] = columns
            index.columns_by_table.setdefault(summary.name, set()).update(columns)
        return index

    def has_table(self, qualified: str) -> bool:
        name = qualified if "." in qualified else f"public.{qualified}"
        return name in self.tables or qualified.split(".")[-1] in self.columns_by_table

    def has_column(self, table: str, column: str) -> bool:
        return column in self.columns_by_table.get(table.split(".")[-1], set())


def validate_semantic(layer: SemanticLayer, index: SchemaIndex) -> list[SemanticIssue]:
    """Return validation issues; an empty list means the layer is valid."""

    issues: list[SemanticIssue] = []
    for name, metric in layer.metrics.items():
        issues.extend(_validate_metric(name, metric, index))
    for name, dimension in layer.dimensions.items():
        issues.extend(_validate_reference("dimension", name, dimension.source, index))
    for name, entity in layer.entities.items():
        if not index.has_table(entity.table):
            issues.append(
                SemanticIssue("error", f"entity.{name}", f"unknown table '{entity.table}'")
            )
        elif not index.has_column(entity.table, entity.primary_key):
            issues.append(
                SemanticIssue(
                    "warning", f"entity.{name}", f"primary key '{entity.primary_key}' not found"
                )
            )
    return issues


def _validate_metric(name: str, metric: Metric, index: SchemaIndex) -> list[SemanticIssue]:
    target = f"metric.{name}"
    issues: list[SemanticIssue] = []

    if not index.has_table(metric.source_table):
        issues.append(
            SemanticIssue("error", target, f"unknown source_table '{metric.source_table}'")
        )

    try:
        expression = sqlglot.parse_one(metric.expression, read="postgres")
    except SqlglotError:
        issues.append(
            SemanticIssue("error", target, f"expression does not parse: {metric.expression!r}")
        )
        expression = None

    if expression is not None:
        for column in expression.find_all(exp.Column):
            if column.table and not index.has_column(column.table, column.name):
                issues.append(
                    SemanticIssue(
                        "warning", target, f"column '{column.table}.{column.name}' not found"
                    )
                )

    for filter_column in metric.filters:
        issues.extend(_validate_reference("metric", name, filter_column, index, level="warning"))
    if metric.time_column:
        issues.extend(
            _validate_reference("metric", name, metric.time_column, index, level="warning")
        )
    return issues


def _validate_reference(
    kind: str, name: str, reference: str, index: SchemaIndex, *, level: str = "error"
) -> list[SemanticIssue]:
    parts = reference.split(".")
    if len(parts) < 2:
        return [SemanticIssue("warning", f"{kind}.{name}", f"'{reference}' is not table.column")]
    table, column = parts[-2], parts[-1]
    if not index.has_column(table, column):
        return [SemanticIssue(level, f"{kind}.{name}", f"column '{table}.{column}' not found")]
    return []
