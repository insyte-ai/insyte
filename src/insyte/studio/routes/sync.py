"""Sync status endpoint (read-only). Starting a sync from Studio arrives in a later stage."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from insyte.services.project_service import ProjectServices
from insyte.studio.dependencies import get_services

router = APIRouter()


@router.get("/sync/status")
def sync_status(services: ProjectServices = Depends(get_services)) -> dict:
    states = services.metadata.list_sync_states()
    return {
        "mode": services.config.analytics.mode.value,
        "tables": [
            {
                "table": s.table,
                "row_count": s.row_count,
                "cursor_column": s.cursor_column,
                "last_cursor": s.last_cursor,
                "mode": s.mode,
            }
            for s in states
        ],
    }


@router.post("/sync/start")
def sync_start() -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={"detail": "Starting a sync from Studio is not yet available; use 'insyte sync'."},
    )
