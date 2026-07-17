"""Structured report generation followed by mandatory grounding review."""

from __future__ import annotations

from collections.abc import Callable

from insyte.agents.critic import CriticAgent
from insyte.agents.schemas import CriticReview
from insyte.nl.llm import Backend
from insyte.studio.schemas import DetailedReport


class ReportAgent:
    def __init__(
        self,
        critic: CriticAgent | None = None,
        resolver: Callable[..., DetailedReport | None] | None = None,
    ) -> None:
        self._critic = critic or CriticAgent()
        self._resolver = resolver

    def generate(
        self, payload: dict, backends: list[Backend] | tuple[Backend, ...]
    ) -> tuple[DetailedReport | None, CriticReview | None]:
        last_review: CriticReview | None = None
        for backend in backends:
            if self._resolver is None:
                from insyte.nl import llm

                report = llm.resolve_report(payload, backend)
            else:
                report = self._resolver(payload, backend)
            if report is None:
                continue
            last_review = self._critic.review(report, payload)
            if last_review.approved:
                return report, last_review
            sanitized = self._critic.sanitize(report, payload)
            sanitized_review = self._critic.review(sanitized, payload)
            if sanitized_review.approved and _has_content(sanitized):
                return sanitized, sanitized_review
        return None, last_review


def _has_content(report: DetailedReport) -> bool:
    data = report.model_dump(mode="json", exclude={"generated_by", "confidence_overall"})
    return any(value not in (None, "", [], {}) for value in data.values())
