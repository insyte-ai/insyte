"""The Insyte Studio FastAPI application factory.

Wires the shared services into API routes, then serves the compiled React frontend. Binds to
localhost only and rejects non-local ``Host`` headers (DNS-rebinding defence). Every analytical
query still flows through the shared safe pipeline — Studio adds no new database path.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from insyte.services.project_service import ProjectService, ProjectServices
from insyte.studio.events import AnalysisFactory
from insyte.studio.routes import (
    analysis,
    conversations,
    exports,
    history,
    metrics,
    project,
    schema,
    sync,
)
from insyte.studio.static import frontend_dir, mount_frontend

_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver", ""}

_API_ROUTERS = (
    project.router,
    conversations.router,
    analysis.router,
    schema.router,
    metrics.router,
    history.router,
    sync.router,
    exports.router,
)


def create_studio_app(
    *,
    services: ProjectServices | None = None,
    project_name: str | None = None,
    analysis_factory: AnalysisFactory | None = None,
    frontend: Path | None = None,
) -> FastAPI:
    """Create the Studio FastAPI app.

    Pass ``services`` to inject pre-built services (tests); otherwise a project is opened.
    """

    owns_services = services is None
    if services is None:
        services = ProjectService.open(project_name)
    factory = analysis_factory or services.build_analysis
    frontend = frontend or frontend_dir()
    resolved_services = services

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owns_services:
            resolved_services.dispose()

    app = FastAPI(
        title="Insyte Studio",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.services = resolved_services
    app.state.analysis_factory = factory
    app.state.pending = {}
    app.state.session_token = secrets.token_urlsafe(24)

    @app.middleware("http")
    async def restrict_host(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        host = request.headers.get("host", "").split(":")[0]
        if host not in _ALLOWED_HOSTS:
            return JSONResponse(status_code=400, content={"detail": "Invalid Host header."})
        return await call_next(request)

    for router in _API_ROUTERS:
        app.include_router(router, prefix="/api")

    mount_frontend(app, frontend)
    return app


# Import string used by ``insyte studio --reload`` (uvicorn re-imports on file change).
STUDIO_PROJECT_ENV = "INSYTE_STUDIO_PROJECT"


def app_from_env() -> FastAPI:
    """Build the app from the ``INSYTE_STUDIO_PROJECT`` env var (for --reload)."""

    return create_studio_app(project_name=os.environ.get(STUDIO_PROJECT_ENV) or None)
