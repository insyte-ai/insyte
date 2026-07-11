"""FastAPI dependencies for the Studio API — access to the shared services and app state."""

from __future__ import annotations

from fastapi import Request

from insyte.config.models import InsyteConfig
from insyte.services.project_service import ProjectServices
from insyte.studio.events import AnalysisFactory


def get_services(request: Request) -> ProjectServices:
    return request.app.state.services


def get_config(request: Request) -> InsyteConfig:
    services: ProjectServices = request.app.state.services
    return services.config


def get_analysis_factory(request: Request) -> AnalysisFactory:
    return request.app.state.analysis_factory


def get_pending(request: Request) -> dict:
    return request.app.state.pending
