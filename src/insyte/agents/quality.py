"""Deterministic metadata and freshness checks."""

from __future__ import annotations

from insyte.agents.schemas import QualityAssessment, QualityIssue
from insyte.metadata.models import ColumnProfile
from insyte.semantic.models import Metric
from insyte.studio.schemas import DataFreshness


class QualityAgent:
    """Assess only quality facts present in persisted metadata."""

    def __init__(self, profiles: list[ColumnProfile]) -> None:
        self._profiles = profiles

    def assess(
        self, metric_name: str, metric: Metric, freshness: DataFreshness
    ) -> QualityAssessment:
        issues: list[QualityIssue] = []
        if freshness.last_scan is None:
            issues.append(
                QualityIssue(
                    issue="Schema freshness is unknown",
                    severity="warning",
                    affected=metric_name,
                    impact="The analysis cannot confirm when metadata was last refreshed.",
                )
            )

        table = metric.source_table.split(".")[-1]
        relevant = [profile for profile in self._profiles if profile.table == table]
        time_column = metric.time_column.split(".")[-1] if metric.time_column else None
        for profile in relevant:
            if profile.null_fraction <= 0:
                continue
            if profile.column != time_column and profile.column not in metric.expression:
                continue
            severity = "critical" if profile.null_fraction >= 0.5 else "warning"
            issues.append(
                QualityIssue(
                    issue=f"{profile.column} contains null values",
                    severity=severity,
                    affected=f"{profile.schema}.{profile.table}.{profile.column}",
                    impact=(
                        f"The persisted profile reports {profile.null_fraction:.0%} nulls in a "
                        "field used by this metric."
                    ),
                )
            )

        summary = (
            "; ".join(issue.impact for issue in issues)
            if issues
            else "No deterministic quality warnings were found for the selected metric."
        )
        return QualityAssessment(issues=issues, summary=summary)
