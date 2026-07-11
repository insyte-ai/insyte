"""Schema endpoints — read scanned metadata via the shared SchemaService."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from insyte.metadata.models import RelationshipInfo, TableSummary
from insyte.services.project_service import ProjectServices
from insyte.studio.dependencies import get_services

router = APIRouter()


def _table_summary(summary: TableSummary) -> dict:
    return {
        "schema": summary.schema,
        "name": summary.name,
        "qualified_name": summary.qualified_name,
        "kind": summary.kind,
        "category": summary.category,
        "row_estimate": summary.row_estimate,
        "column_count": summary.column_count,
    }


@router.get("/schema")
def get_schema(services: ProjectServices = Depends(get_services)) -> dict:
    summary = services.schema.database_summary()
    return {
        "scanned": summary.scanned,
        "schemas": summary.schemas,
        "table_count": len(summary.tables),
        "tables": [_table_summary(t) for t in summary.tables],
        "last_scan": summary.last_scan.isoformat() if summary.last_scan else None,
    }


@router.get("/schema/tables")
def list_tables(services: ProjectServices = Depends(get_services)) -> dict:
    return {"tables": [_table_summary(t) for t in services.schema.list_tables()]}


@router.get("/schema/tables/{schema}/{table}")
def table_detail(
    schema: str, table: str, services: ProjectServices = Depends(get_services)
) -> dict:
    detail = services.schema.get_table(schema, table)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Table '{schema}.{table}' not found.")
    return {
        "summary": _table_summary(detail.summary),
        "columns": [
            {
                "name": c.name,
                "type": c.data_type,
                "nullable": c.nullable,
                "primary_key": c.is_primary_key,
                "unique": c.is_unique,
                "comment": c.comment,
            }
            for c in detail.columns
        ],
        "indexes": [
            {"name": i.name, "columns": i.columns, "unique": i.is_unique} for i in detail.indexes
        ],
        "references": [_relationship(r) for r in detail.outgoing],
        "referenced_by": [_relationship(r) for r in detail.incoming],
    }


def _relationship(rel: RelationshipInfo) -> dict:
    return {
        "from": f"{rel.source_qualified}.{','.join(rel.source_columns)}",
        "to": f"{rel.target_qualified}.{','.join(rel.target_columns)}",
        "kind": rel.kind,
        "confidence": rel.confidence,
    }
