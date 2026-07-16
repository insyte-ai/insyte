"""Strict messages exchanged by internal analytics agents."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class _AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentRole(StrEnum):
    planner = "planner"
    analyst = "analyst"
    quality = "quality"
    report = "report"
    critic = "critic"


class AnalysisOperation(StrEnum):
    trend = "trend"
    comparison = "comparison"
    segment = "segment"
    quality = "quality"
    report = "report"


class AgentMessage(_AgentModel):
    role: AgentRole
    task: str
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)


class PlannerDecision(_AgentModel):
    metric: str
    dimension: str | None = None
    operations: list[AnalysisOperation] = Field(min_length=1, max_length=5)
    rationale: str = ""
    confidence: str = "medium"
    generated_by: str = "deterministic"


class QualityIssue(_AgentModel):
    issue: str
    severity: str
    affected: str
    impact: str


class QualityAssessment(_AgentModel):
    issues: list[QualityIssue] = Field(default_factory=list)
    summary: str = "No deterministic quality warnings were found."


class CriticReview(_AgentModel):
    approved: bool
    unsupported_claims: list[str] = Field(default_factory=list)
    action: str = "accept"
    confidence: str = "high"
