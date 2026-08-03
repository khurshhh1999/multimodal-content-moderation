from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Query

from ..db import connection
from ..schemas import AuditEventOut

router = APIRouter(prefix="/v1", tags=["audit"])


def _row_to_audit(row) -> AuditEventOut:
    detail = row["detail"]
    if isinstance(detail, str):
        detail = json.loads(detail)
    return AuditEventOut(
        id=row["id"],
        entity_type=row["entity_type"],
        entity_id=row["entity_id"],
        action=row["action"],
        actor=row["actor"],
        detail=dict(detail or {}),
        created_at=row["created_at"],
    )


@router.get("/audit", response_model=list[AuditEventOut])
async def list_audit(
    entity_type: str | None = Query(default=None),
    entity_id: UUID | None = Query(default=None),
    actor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AuditEventOut]:
    clauses: list[str] = []
    args: list = []

    if entity_type:
        args.append(entity_type)
        clauses.append(f"entity_type = ${len(args)}")
    if entity_id is not None:
        args.append(entity_id)
        clauses.append(f"entity_id = ${len(args)}")
    if actor:
        args.append(actor)
        clauses.append(f"actor = ${len(args)}")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    args.append(limit)
    query = f"""
        SELECT id, entity_type, entity_id, action, actor, detail, created_at
        FROM audit_log
        {where}
        ORDER BY created_at DESC
        LIMIT ${len(args)}
    """

    async with connection() as conn:
        rows = await conn.fetch(query, *args)
    return [_row_to_audit(r) for r in rows]
