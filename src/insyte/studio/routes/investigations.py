"""Saved investigation endpoints for Studio workspace routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from insyte.metadata.models import SavedInvestigation
from insyte.services.project_service import ProjectServices
from insyte.studio.dependencies import get_services
from insyte.studio.schemas import TitleUpdate

router = APIRouter()


def _investigation(inv: SavedInvestigation, *, include_result: bool = False) -> dict:
    payload = {
        "id": inv.id,
        "analysis_id": inv.analysis_id,
        "conversation_id": inv.conversation_id,
        "title": inv.title,
        "summary": inv.summary,
        "question": inv.question,
        "created_at": inv.created_at.isoformat(),
        "updated_at": inv.updated_at.isoformat(),
    }
    if include_result:
        payload["result"] = inv.result_json
    return payload


@router.get("/investigations")
def list_investigations(services: ProjectServices = Depends(get_services)) -> dict:
    return {
        "investigations": [
            _investigation(inv) for inv in services.conversations.investigations()
        ]
    }


@router.get("/investigations/{investigation_id}")
def get_investigation(
    investigation_id: str, services: ProjectServices = Depends(get_services)
) -> dict:
    inv = services.conversations.investigation(investigation_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Investigation not found.")
    return {"investigation": _investigation(inv, include_result=True)}


@router.post("/investigations/{investigation_id}/rename")
def rename_investigation(
    investigation_id: str,
    body: TitleUpdate,
    services: ProjectServices = Depends(get_services),
) -> dict:
    title = " ".join(body.title.split())
    if not title:
        raise HTTPException(status_code=400, detail="Title is required.")
    if not services.conversations.set_investigation_title(investigation_id, title):
        raise HTTPException(status_code=404, detail="Investigation not found.")
    return {"renamed": True, "title": title}


@router.delete("/investigations/{investigation_id}")
def delete_investigation(
    investigation_id: str, services: ProjectServices = Depends(get_services)
) -> dict:
    if not services.conversations.delete_investigation(investigation_id):
        raise HTTPException(status_code=404, detail="Investigation not found.")
    return {"deleted": True}
