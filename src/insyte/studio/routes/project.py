"""Health, project, status and redacted public config."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from insyte.config.models import InsyteConfig
from insyte.config.secrets import database_url_is_available
from insyte.services.project_service import ProjectServices
from insyte.studio.dependencies import get_config, get_services

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/project")
def project(config: InsyteConfig = Depends(get_config)) -> dict:
    return {
        "name": config.project.name,
        "database": config.database.type.value,
        "analytics_mode": config.analytics.mode.value,
    }


@router.get("/status")
def status(services: ProjectServices = Depends(get_services)) -> dict:
    config = services.config
    scanned = services.schema.has_metadata()
    latest = services.schema.latest_scan() if scanned else None
    tables = len(services.schema.list_tables()) if scanned else 0
    return {
        "project": config.project.name,
        "database": {
            "type": config.database.type.value,
            "url_configured": database_url_is_available(config.database, config.project.name),
        },
        "schema": {
            "scanned": scanned,
            "tables": tables,
            "last_scan": latest.finished_at.isoformat() if latest else None,
        },
        "analytics_mode": config.analytics.mode.value,
        "read_only": True,
    }


@router.get("/config/public")
def public_config(config: InsyteConfig = Depends(get_config)) -> dict:
    """A redacted view of config — never passwords, URLs, or env-var values."""

    return {
        "project": config.project.name,
        "database": {
            "type": config.database.type.value,
            "ssl_mode": config.database.ssl_mode.value,
            "allowed_schemas": config.database.allowed_schemas,
            "url_env": config.database.url_env,  # the variable name, never its value
            "url_configured": database_url_is_available(config.database, config.project.name),
        },
        "analytics": {"mode": config.analytics.mode.value},
        "query": {
            "default_limit": config.query.default_limit,
            "maximum_limit": config.query.maximum_limit,
            "timeout_seconds": config.query.timeout_seconds,
        },
        "privacy": {"mask_pii": config.privacy.mask_pii, "telemetry": config.privacy.telemetry},
    }
