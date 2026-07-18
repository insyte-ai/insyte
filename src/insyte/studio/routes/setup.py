"""Browser-first project setup and local release checks."""

from __future__ import annotations

import secrets
import shutil
import threading
from dataclasses import asdict
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from insyte.config import loader, paths
from insyte.config.models import AIClient, AnalyticsMode, SSLMode
from insyte.exceptions import InsyteError
from insyte.services.project_service import ProjectService
from insyte.services.provider_auth_service import ProviderName

router = APIRouter()


def _require_local_session(request: Request) -> None:
    supplied = request.headers.get("x-insyte-session", "")
    if not supplied or not secrets.compare_digest(supplied, request.app.state.session_token):
        raise HTTPException(status_code=403, detail="Invalid local Studio session.")


class ProjectSetupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    database_url: str = Field(min_length=1)
    schemas: list[str] = Field(default_factory=lambda: ["public"])
    ssl_mode: SSLMode = SSLMode.prefer
    analytics_mode: AnalyticsMode = AnalyticsMode.direct
    ai_client: Literal["claude", "codex", "off"] = "off"

    @field_validator("schemas")
    @classmethod
    def _clean_schemas(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if not cleaned:
            raise ValueError("At least one schema is required.")
        if any(not item.replace("_", "").isalnum() for item in cleaned):
            raise ValueError("Schema names may contain only letters, numbers, and underscores.")
        return cleaned


@router.get("/setup/status")
def setup_status(request: Request) -> dict:
    services = request.app.state.services
    projects = loader.list_projects()
    return {
        "needs_setup": services is None,
        "projects": projects,
        "active_project": services.config.project.name if services else paths.get_active_project(),
        "providers": {
            "claude": {"installed": shutil.which("claude") is not None},
            "codex": {"installed": shutil.which("codex") is not None},
        },
        "session_token": request.app.state.session_token,
    }


@router.post("/setup/projects")
def create_project(body: ProjectSetupRequest, request: Request) -> dict:
    _require_local_session(request)
    if request.app.state.services is not None:
        raise HTTPException(status_code=409, detail="A project is already active.")
    ai_client = None if body.ai_client == "off" else AIClient(body.ai_client)
    try:
        config, check = request.app.state.setup_service.create_project(
            name=body.name.strip(),
            database_url=body.database_url,
            schemas=body.schemas,
            ssl_mode=body.ssl_mode,
            analytics_mode=body.analytics_mode,
            ai_client=ai_client,
        )
        services = ProjectService.open(config.project.name)
    except InsyteError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    request.app.state.services = services
    request.app.state.analysis_factory = services.build_analysis
    return {
        "project": config.project.name,
        "connection": {
            "database": check.server.database,
            "user": check.server.user,
            "postgresql": check.server.is_postgres,
            "ssl": check.ssl.in_use,
            "read_only_enforced": check.read_only_enforced,
            "has_write_access": check.permissions.has_write_access,
            "warnings": check.warnings,
        },
    }


@router.post("/setup/projects/{project}/open")
def open_project(project: str, request: Request) -> dict:
    """Open a saved local project from browser-first onboarding."""

    _require_local_session(request)
    if request.app.state.services is not None:
        raise HTTPException(status_code=409, detail="A project is already active.")
    try:
        services = ProjectService.open(project)
    except InsyteError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    paths.set_active_project(services.config.project.name)
    request.app.state.services = services
    request.app.state.analysis_factory = services.build_analysis
    return {"project": services.config.project.name}


@router.get("/setup/providers/{provider}")
def provider_status(provider: ProviderName, request: Request) -> dict:
    return request.app.state.provider_auth_service.public_status(provider)


@router.post("/setup/providers/{provider}/login")
def provider_login(provider: ProviderName, request: Request) -> dict:
    _require_local_session(request)
    status = request.app.state.provider_auth_service.status(provider)
    if not status.installed:
        raise HTTPException(status_code=409, detail=status.detail)
    if status.authenticated:
        return {"authenticated": True, "job_id": None}
    return {
        "authenticated": False,
        "job_id": request.app.state.provider_auth_service.begin_login(provider),
    }


@router.post("/setup/disconnect")
def disconnect_project(request: Request) -> dict:
    """Return Studio to onboarding while preserving every existing project."""

    _require_local_session(request)
    services = request.app.state.services
    if services is None:
        return {"disconnected": False, "projects": loader.list_projects()}

    # Detach state before disposal so no new request can acquire the old services.
    request.app.state.services = None
    request.app.state.analysis_factory = None
    request.app.state.pending.clear()
    request.app.state.setup_jobs.clear()
    paths.clear_active_project()
    services.dispose()
    return {"disconnected": True, "projects": loader.list_projects()}


@router.get("/setup/provider-jobs/{job_id}")
def provider_job(job_id: str, request: Request) -> dict:
    _require_local_session(request)
    job = request.app.state.provider_auth_service.job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Provider login job not found.")
    return job


@router.post("/setup/run")
def run_setup(request: Request) -> dict:
    _require_local_session(request)
    services = request.app.state.services
    if services is None:
        raise HTTPException(status_code=409, detail="Create a project first.")
    job_id = "setup_" + uuid4().hex[:12]
    job: dict[str, Any] = {
        "id": job_id,
        "status": "running",
        "step": "starting",
        "message": "Starting setup",
        "result": None,
        "error": None,
    }
    request.app.state.setup_jobs[job_id] = job
    setup_service = request.app.state.setup_service
    project = services.config.project.name

    def progress(step: str, message: str) -> None:
        job.update(step=step, message=message)

    def work() -> None:
        try:
            outcome = setup_service.initialise(project, progress=progress)
            job.update(status="completed", result=asdict(outcome))
        except InsyteError as exc:
            job.update(status="failed", error=str(exc))
        except Exception:  # noqa: BLE001 - background boundary must remain alive
            job.update(status="failed", error="Setup failed. Check the local Insyte log.")

    threading.Thread(target=work, daemon=True, name=job_id).start()
    return {"job_id": job_id}


@router.get("/setup/jobs/{job_id}")
def setup_job(job_id: str, request: Request) -> dict:
    job = request.app.state.setup_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Setup job not found.")
    return job


@router.get("/updates/check")
def check_updates(request: Request) -> dict:
    return asdict(request.app.state.update_service.check())
