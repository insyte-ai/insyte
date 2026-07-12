"""Structured conversation context for Studio follow-up analysis.

The context is intentionally compact and deterministic. It records enough state to resolve
follow-ups without replaying a long chat or exposing raw result rows to an AI backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from insyte.studio.schemas import AnalysisResult

MAX_RECENT_TURNS = 6


@dataclass
class ChatContext:
    active_metric: str | None = None
    active_dimension: str | None = None
    active_period: str | None = None
    last_analysis_id: str | None = None
    last_result_summary: str | None = None
    active_report_mode: str = "standard"
    unresolved_assumptions: list[str] = field(default_factory=list)
    recent_turns: list[dict[str, str]] = field(default_factory=list)
    rolling_summary: str = ""
    analysis_summaries: list[dict[str, str]] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        return {
            "active_metric": self.active_metric,
            "active_dimension": self.active_dimension,
            "active_period": self.active_period,
            "last_analysis_id": self.last_analysis_id,
            "last_result_summary": self.last_result_summary,
            "active_report_mode": self.active_report_mode,
            "unresolved_assumptions": list(self.unresolved_assumptions),
            "recent_turns": list(self.recent_turns),
            "rolling_summary": self.rolling_summary,
            "analysis_summaries": list(self.analysis_summaries),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> ChatContext:
        if not data:
            return cls()
        updated = data.get("updated_at")
        if isinstance(updated, str):
            try:
                updated_at = datetime.fromisoformat(updated)
            except ValueError:
                updated_at = datetime.now(UTC)
        else:
            updated_at = datetime.now(UTC)
        return cls(
            active_metric=_str_or_none(data.get("active_metric")),
            active_dimension=_str_or_none(data.get("active_dimension")),
            active_period=_str_or_none(data.get("active_period")),
            last_analysis_id=_str_or_none(data.get("last_analysis_id")),
            last_result_summary=_str_or_none(data.get("last_result_summary")),
            active_report_mode=str(data.get("active_report_mode") or "standard"),
            unresolved_assumptions=[
                str(v) for v in data.get("unresolved_assumptions", []) if str(v).strip()
            ],
            recent_turns=[
                {"role": str(t.get("role", "")), "content": str(t.get("content", ""))}
                for t in data.get("recent_turns", [])
                if isinstance(t, dict)
            ][-MAX_RECENT_TURNS:],
            rolling_summary=str(data.get("rolling_summary") or ""),
            analysis_summaries=[
                {
                    "analysis_id": str(t.get("analysis_id", "")),
                    "summary": str(t.get("summary", "")),
                }
                for t in data.get("analysis_summaries", [])
                if isinstance(t, dict)
            ][-MAX_RECENT_TURNS:],
            updated_at=updated_at,
        )

    def prompt_summary(self) -> str:
        parts = []
        if self.active_metric:
            parts.append(f"active_metric={self.active_metric}")
        if self.active_dimension:
            parts.append(f"active_dimension={self.active_dimension}")
        if self.active_period:
            parts.append(f"active_period={self.active_period}")
        if self.last_result_summary:
            parts.append(f"last_result_summary={self.last_result_summary}")
        if self.unresolved_assumptions:
            parts.append("unresolved_assumptions=" + "; ".join(self.unresolved_assumptions))
        return "\n".join(parts)


def build_chat_context(
    *,
    question: str,
    result: AnalysisResult,
    previous: ChatContext | None = None,
    active_metric: str | None = None,
    active_dimension: str | None = None,
    active_period: str | None = None,
    detailed: bool = False,
) -> ChatContext:
    """Build the next compact context snapshot from the completed Studio result."""

    base = previous or ChatContext()
    summary = _compact_summary(result)
    recent_turns = [
        *base.recent_turns,
        {"role": "user", "content": _clip(question, 240)},
        {"role": "assistant", "content": _clip(summary or result.summary, 240)},
    ][-MAX_RECENT_TURNS:]
    analysis_summaries = [
        *base.analysis_summaries,
        {"analysis_id": result.analysis_id, "summary": _clip(summary or result.summary, 320)},
    ][-MAX_RECENT_TURNS:]
    rolling = _rolling_summary(base.rolling_summary, question, summary or result.summary)
    return ChatContext(
        active_metric=active_metric or base.active_metric,
        active_dimension=active_dimension or base.active_dimension,
        active_period=active_period or base.active_period,
        last_analysis_id=result.analysis_id,
        last_result_summary=summary or result.summary,
        active_report_mode="detailed" if detailed else "standard",
        unresolved_assumptions=_assumptions(result),
        recent_turns=recent_turns,
        rolling_summary=rolling,
        analysis_summaries=analysis_summaries,
    )


def _compact_summary(result: AnalysisResult) -> str:
    bits = [result.summary]
    if result.metrics:
        metric = result.metrics[0]
        bits.append(f"{metric.label}={metric.value}")
    if result.contributors:
        top = result.contributors[0]
        bits.append(f"top contributor {top.label} ({top.contribution_percent}%)")
    return _clip("; ".join(b for b in bits if b), 500)


def _rolling_summary(previous: str, question: str, summary: str) -> str:
    addition = f"Q: {_clip(question, 140)} A: {_clip(summary, 220)}"
    return _clip((previous + "\n" + addition).strip(), 1200)


def _assumptions(result: AnalysisResult) -> list[str]:
    assumptions: list[str] = []
    if result.status != "completed":
        assumptions.append("Last request did not complete as an analysis.")
    if result.warnings:
        assumptions.extend(result.warnings[:3])
    if result.freshness and result.freshness.last_scan is None:
        assumptions.append("No completed schema scan timestamp is available.")
    return assumptions[:5]


def _clip(value: str, limit: int) -> str:
    value = " ".join(value.split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
