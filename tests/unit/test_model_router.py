"""Tests for task-aware local model routing."""

from __future__ import annotations

from insyte.config.models import AISection
from insyte.nl.llm import Backend
from insyte.nl.router import ModelRouter, ModelTask


def test_task_route_uses_independent_backend_and_explicit_fallback() -> None:
    seen: list[str] = []

    def resolve(preference: str) -> list[Backend]:
        seen.append(preference)
        return [] if preference == "claude" else [Backend("codex", ["codex"])]

    router = ModelRouter(
        AISection(intent_backend="claude", fallback_backend="codex"), resolver=resolve
    )
    route = router.route(ModelTask.intent)

    assert seen == ["claude", "codex"]
    assert [backend.name for backend in route.backends] == ["codex"]
    assert route.deterministic is False


def test_off_route_uses_deterministic_path() -> None:
    router = ModelRouter(AISection(report_backend="off"), resolver=lambda _pref: [])
    route = router.route(ModelTask.report)

    assert route.backends == ()
    assert route.deterministic is True


def test_task_auto_inherits_explicit_legacy_studio_backend() -> None:
    seen: list[str] = []

    def resolve(preference: str) -> list[Backend]:
        seen.append(preference)
        return [Backend(preference, [preference])]

    ModelRouter(AISection(studio_backend="codex"), resolver=resolve).route(ModelTask.planner)

    assert seen == ["codex"]
