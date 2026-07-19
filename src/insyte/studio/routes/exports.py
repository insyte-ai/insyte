"""Export endpoints — CSV of an analysis result (PNG/HTML render client-side)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from insyte.services.export_service import result_table_to_csv, result_table_to_xlsx
from insyte.services.pdf_service import result_to_pdf
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


@router.post("/analyses/{analysis_id}/exports/xlsx")
def export_xlsx(
    analysis_id: str, services: ProjectServices = Depends(get_services)
) -> Response:
    stored = services.conversations.get_analysis(analysis_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    workbook = result_table_to_xlsx(json.loads(stored))
    return Response(
        workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{analysis_id}.xlsx"'},
    )


@router.post("/analyses/{analysis_id}/exports/pdf")
def export_pdf(
    analysis_id: str, services: ProjectServices = Depends(get_services)
) -> Response:
    stored = services.conversations.get_analysis(analysis_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Analysis not found.")
    return Response(
        result_to_pdf(json.loads(stored)),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{analysis_id}.pdf"'},
    )


@router.post("/analyses/{analysis_id}/exports/{fmt}")
def export_other(analysis_id: str, fmt: str) -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={"detail": f"Export format '{fmt}' is rendered client-side in Studio."},
    )
