"""Task-aware routing for local AI clients.

The router selects model clients only. It never receives credentials, executes SQL, or bypasses
the deterministic analytics services that validate every operation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from insyte.config.models import AISection
from insyte.nl.llm import Backend, available_backends

logger = logging.getLogger(__name__)


class ModelTask(StrEnum):
    """AI tasks that may be routed independently."""

    intent = "intent"
    planner = "planner"
    report = "report"


@dataclass(frozen=True)
class ModelRoute:
    """Auditable routing decision for one task."""

    task: ModelTask
    requested: str
    backends: tuple[Backend, ...]
    fallback: str

    @property
    def deterministic(self) -> bool:
        return not self.backends


class ModelRouter:
    """Resolve configured task routes to installed local AI clients."""

    def __init__(
        self,
        config: AISection,
        resolver: Callable[[str], list[Backend]] = available_backends,
    ) -> None:
        self._config = config
        self._resolver = resolver

    def route(self, task: ModelTask) -> ModelRoute:
        requested = getattr(self._config, f"{task.value}_backend")
        effective = requested
        if requested == "auto" and self._config.studio_backend not in {"auto", "off"}:
            effective = self._config.studio_backend

        candidates = self._resolver(effective)
        fallback = self._config.fallback_backend
        if fallback not in {"off", "auto", effective}:
            existing = {backend.name for backend in candidates}
            candidates.extend(
                backend for backend in self._resolver(fallback) if backend.name not in existing
            )
        elif fallback == "auto" and effective != "auto":
            existing = {backend.name for backend in candidates}
            candidates.extend(
                backend for backend in self._resolver("auto") if backend.name not in existing
            )

        route = ModelRoute(task, requested, tuple(candidates), fallback)
        logger.info(
            "model_route_resolved",
            extra={
                "task": task.value,
                "requested": requested,
                "backends": [backend.name for backend in route.backends],
                "fallback": fallback,
                "deterministic": route.deterministic,
            },
        )
        return route
