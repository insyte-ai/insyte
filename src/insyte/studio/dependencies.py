"""FastAPI dependencies for the Studio API — access to the shared services and app state."""

from __future__ import annotations

from fastapi import HTTPException, Request

from insyte.config.models import InsyteConfig
from insyte.services.project_service import ProjectServices
from insyte.studio.events import AnalysisFactory


def get_services(request: Request) -> ProjectServices:
    services: ProjectServices | None = request.app.state.services
    if services is None:
        raise HTTPException(status_code=409, detail="Complete Studio setup first.")
    return services


def get_config(request: Request) -> InsyteConfig:
    return get_services(request).config


def get_analysis_factory(request: Request) -> AnalysisFactory:
    factory: AnalysisFactory | None = request.app.state.analysis_factory
    if factory is None:
        raise HTTPException(status_code=409, detail="Complete Studio setup first.")
    return factory


def get_pending(request: Request) -> dict:
    return request.app.state.pending
