"""Tests for the typed configuration models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from insyte.config.models import (
    AIClient,
    AnalyticsMode,
    DatabaseType,
    InsyteConfig,
    ProjectSection,
    SSLMode,
)


def _minimal() -> InsyteConfig:
    return InsyteConfig(project=ProjectSection(name="demo"))


def test_defaults_match_spec() -> None:
    config = _minimal()
    assert config.database.type is DatabaseType.postgresql
    assert config.database.url_env == "INSYTE_DATABASE_URL"
    assert config.database.allowed_schemas == ["public"]
    assert config.database.ssl_mode is SSLMode.require
    assert config.query.default_limit == 500
    assert config.query.maximum_limit == 5000
    assert config.query.timeout_seconds == 20
    assert config.analytics.mode is AnalyticsMode.direct
    assert config.privacy.telemetry is False
    assert config.privacy.persist_raw_results is False
    assert config.ai.integration == []
    assert config.ai.intent_backend == "auto"
    assert config.ai.report_backend == "auto"
    assert config.ai.planner_backend == "auto"
    assert config.ai.fallback_backend == "off"


def test_enum_coercion_from_strings() -> None:
    config = InsyteConfig.model_validate(
        {
            "project": {"name": "demo"},
            "database": {"ssl_mode": "verify-full"},
            "analytics": {"mode": "local"},
            "ai": {"integration": ["claude", "codex"]},
        }
    )
    assert config.database.ssl_mode is SSLMode.verify_full
    assert config.analytics.mode is AnalyticsMode.local
    assert config.ai.integration == [AIClient.claude, AIClient.codex]


def test_blocked_columns_parse() -> None:
    config = InsyteConfig.model_validate(
        {
            "project": {"name": "demo"},
            "database": {"blocked_columns": ["users.password_hash", "public.users.auth_token"]},
        }
    )
    assert "users.password_hash" in config.database.blocked_columns


def test_blocked_columns_reject_unqualified() -> None:
    with pytest.raises(ValidationError):
        InsyteConfig.model_validate(
            {"project": {"name": "demo"}, "database": {"blocked_columns": ["password_hash"]}}
        )


def test_invalid_analytics_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        InsyteConfig.model_validate(
            {"project": {"name": "demo"}, "analytics": {"mode": "warp-drive"}}
        )


def test_invalid_database_type_rejected() -> None:
    with pytest.raises(ValidationError):
        InsyteConfig.model_validate({"project": {"name": "demo"}, "database": {"type": "mysql"}})


def test_unknown_key_rejected() -> None:
    with pytest.raises(ValidationError):
        InsyteConfig.model_validate({"project": {"name": "demo"}, "surprise": 1})


def test_invalid_ai_backend_rejected() -> None:
    with pytest.raises(ValidationError):
        InsyteConfig.model_validate(
            {"project": {"name": "demo"}, "ai": {"planner_backend": "remote-magic"}}
        )


def test_default_limit_cannot_exceed_maximum() -> None:
    with pytest.raises(ValidationError):
        InsyteConfig.model_validate(
            {"project": {"name": "demo"}, "query": {"default_limit": 9000, "maximum_limit": 100}}
        )


def test_round_trip_to_yaml_dict() -> None:
    config = _minimal()
    data = config.to_yaml_dict()
    # Enums are rendered as plain strings, ready for YAML.
    assert data["database"]["ssl_mode"] == "require"
    assert data["analytics"]["mode"] == "direct"
    assert InsyteConfig.model_validate(data) == config


def test_no_password_field_exists() -> None:
    # Defence in depth: the schema must never grow a credential field.
    fields = set(InsyteConfig.model_json_schema()["$defs"]["DatabaseSection"]["properties"])
    assert not fields & {"password", "url", "dsn", "secret"}
