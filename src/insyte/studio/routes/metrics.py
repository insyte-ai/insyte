"""Metric endpoints — read the semantic layer via the shared MetricService."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from insyte.services.project_service import ProjectServices
from insyte.studio.dependencies import get_services

router = APIRouter()


@router.get("/metrics")
def list_metrics(services: ProjectServices = Depends(get_services)) -> dict:
    layer = services.metrics.layer()
    return {
        "metrics": [
            {
                "name": name,
                "label": m.label,
                "expression": m.expression,
                "source_table": m.source_table,
                "status": m.status.value,
                "format": m.format.value,
                "confidence": m.confidence,
            }
            for name, m in sorted(layer.metrics.items())
        ],
        "dimensions": [
            {"name": name, "source": d.source, "type": d.type}
            for name, d in sorted(layer.dimensions.items())
        ],
    }


@router.get("/metrics/{name}")
def get_metric(name: str, services: ProjectServices = Depends(get_services)) -> dict:
    metric = services.metrics.get(name)
    if metric is None:
        raise HTTPException(status_code=404, detail=f"Metric '{name}' not found.")
    return {
        "name": name,
        "label": metric.label,
        "expression": metric.expression,
        "source_table": metric.source_table,
        "filters": metric.filters,
        "time_column": metric.time_column,
        "status": metric.status.value,
        "format": metric.format.value,
    }
