"""Query-history endpoints — read the audit log via the shared HistoryService."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from insyte.services.project_service import ProjectServices
from insyte.studio.dependencies import get_services

router = APIRouter()


@router.get("/history")
def get_history(limit: int = 50, services: ProjectServices = Depends(get_services)) -> dict:
    queries = services.history.queries(limit)
    events = services.history.events(limit)
    return {
        "queries": [
            {
                "created_at": q.created_at.isoformat() if q.created_at else None,
                "source": q.source,
                "status": q.status,
                "row_count": q.row_count,
                "duration_ms": q.duration_ms,
                "sql": q.raw_sql,
            }
            for q in queries
        ],
        "security_events": [
            {
                "created_at": e.created_at.isoformat() if e.created_at else None,
                "type": e.event_type,
                "violations": e.violations,
            }
            for e in events
        ],
    }
