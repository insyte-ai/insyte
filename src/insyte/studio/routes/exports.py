"""Export endpoints — CSV of an analysis result (PNG/HTML render client-side)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from insyte.services.export_service import result_table_to_csv
from insyte.services.project_service import ProjectServices
from insyte.studio.dependencies import get_services

router = APIRouter()


@router.post("/analyses/{analysis_id}/exports/csv")
def export_csv(
    analysis_id: str, services: ProjectServices = Depends(get_services)
) -> PlainTextResponse:
    stored = services.conversations.get_analysis(analysis_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    csv_text = result_table_to_csv(json.loads(stored))
    return PlainTextResponse(
        csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{analysis_id}.csv"'},
    )


@router.post("/analyses/{analysis_id}/exports/{fmt}")
def export_other(analysis_id: str, fmt: str) -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={"detail": f"Export format '{fmt}' is rendered client-side in Studio."},
    )
