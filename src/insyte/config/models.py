"""Strongly typed configuration models for Insyte projects.

These Pydantic v2 models mirror the ``config.yaml`` structure exactly. The raw database
password is deliberately absent from every model: configuration only ever stores the *name*
of the environment variable that holds the connection URL, never a credential.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DatabaseType(StrEnum):
    """Supported database engines. Version 0.1.0 ships PostgreSQL only."""

    postgresql = "postgresql"


class SSLMode(StrEnum):
    """PostgreSQL ``sslmode`` values."""

    disable = "disable"
    allow = "allow"
    prefer = "prefer"
    require = "require"
    verify_ca = "verify-ca"
    verify_full = "verify-full"


class AnalyticsMode(StrEnum):
    """How analytical queries are executed."""

    direct = "direct"
    local = "local"


class AIClient(StrEnum):
    """AI clients Insyte can integrate with over MCP."""

    claude = "claude"
    codex = "codex"


class _StrictModel(BaseModel):
    """Base model that forbids unknown keys so typos in ``config.yaml`` surface loudly."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class ProjectSection(_StrictModel):
    """Top-level project identity."""

    name: str

    @field_validator("name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("project.name must not be empty")
        return value


class DatabaseSection(_StrictModel):
    """Database connection configuration. Stores only the env var name, never the URL."""

    type: DatabaseType = DatabaseType.postgresql
    url_env: str = "INSYTE_DATABASE_URL"
    allowed_schemas: list[str] = Field(default_factory=lambda: ["public"])
    blocked_tables: list[str] = Field(default_factory=list)
    blocked_columns: list[str] = Field(default_factory=list)
    ssl_mode: SSLMode = SSLMode.require

    @field_validator("blocked_columns")
    @classmethod
    def _validate_blocked_columns(cls, value: list[str]) -> list[str]:
        for item in value:
            if item.count(".") < 1 or " " in item:
                raise ValueError(
                    f"blocked_columns entry {item!r} must be qualified as 'table.column' "
                    "or 'schema.table.column'"
                )
        return value


class QuerySection(_StrictModel):
    """Guardrails applied to every generated query."""

    default_limit: int = Field(default=500, gt=0)
    maximum_limit: int = Field(default=5000, gt=0)
    timeout_seconds: int = Field(default=20, gt=0)
    lock_timeout_seconds: int = Field(default=3, gt=0)
    maximum_result_bytes: int = Field(default=10_000_000, gt=0)
    allow_direct_query: bool = True

    @model_validator(mode="after")
    def _limits_consistent(self) -> QuerySection:
        if self.default_limit > self.maximum_limit:
            raise ValueError("query.default_limit must not exceed query.maximum_limit")
        return self


class ProfilingSection(_StrictModel):
    """Controls for safe data profiling."""

    enabled: bool = True
    sample_rows: int = Field(default=10_000, gt=0)
    maximum_tables: int = Field(default=100, gt=0)
    maximum_columns_per_table: int = Field(default=200, gt=0)
    detect_pii: bool = True


class AnalyticsSection(_StrictModel):
    """Analytics execution mode and local warehouse location."""

    mode: AnalyticsMode = AnalyticsMode.direct
    duckdb_path: str = "analytics.duckdb"


class PrivacySection(_StrictModel):
    """Privacy defaults. Telemetry is off and raw results are not persisted."""

    mask_pii: bool = True
    persist_raw_results: bool = False
    telemetry: bool = False


class AISection(_StrictModel):
    """Which AI clients this project integrates with."""

    integration: list[AIClient] = Field(default_factory=list)
    # Which local AI CLI powers Studio/TUI free-form questions: auto | claude | codex | off.
    # 'auto' uses whichever of claude/codex is installed; 'off' keeps the deterministic parser.
    studio_backend: str = "auto"
    # Task-specific routes. ``auto`` inherits an explicit legacy studio_backend when present,
    # otherwise it tries installed local clients in their normal order.
    intent_backend: str = "auto"
    report_backend: str = "auto"
    planner_backend: str = "auto"
    # An explicit fallback is opt-in. ``off`` means a failed task route falls back to the
    # deterministic application path, not another model.
    fallback_backend: str = "off"
    # Global kill-switch for opt-in detailed reports (aggregated results sent to the local AI
    # CLI for analyst commentary). The per-request toggle can only narrow this, never widen it.
    detailed_reports: bool = True

    @field_validator(
        "studio_backend",
        "intent_backend",
        "report_backend",
        "planner_backend",
        "fallback_backend",
    )
    @classmethod
    def _valid_backend(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"auto", "claude", "codex", "off"}:
            raise ValueError("AI backends must be auto, claude, codex, or off")
        return normalized


class InsyteConfig(_StrictModel):
    """Root configuration for a single Insyte project."""

    project: ProjectSection
    database: DatabaseSection = Field(default_factory=DatabaseSection)
    query: QuerySection = Field(default_factory=QuerySection)
    profiling: ProfilingSection = Field(default_factory=ProfilingSection)
    analytics: AnalyticsSection = Field(default_factory=AnalyticsSection)
    privacy: PrivacySection = Field(default_factory=PrivacySection)
    ai: AISection = Field(default_factory=AISection)

    def to_yaml_dict(self) -> dict[str, Any]:
        """Return a plain, YAML-serialisable dict with enums rendered as their values."""

        return self.model_dump(mode="json")
