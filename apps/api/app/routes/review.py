from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query

from ..config import get_settings
from ..db import connection
from ..schemas import ClaimRequest, DecisionOut, ResolveRequest, ReviewItemOut
from ..storage import public_url

router = APIRouter(prefix="/v1", tags=["review"])

CLAIM_TTL_MINUTES = 15


def _row_to_review(row: Any, settings) -> ReviewItemOut:
    reasons = row["reasons"]
    if isinstance(reasons, str):
        reasons = json.loads(reasons)
    vision = row["vision_signals"]
    llm = row["llm_signals"]
    if isinstance(vision, str):
        vision = json.loads(vision)
    if isinstance(llm, str):
        llm = json.loads(llm)
    return ReviewItemOut(
        id=row["id"],
        decision_id=row["decision_id"],
        content_id=row["content_id"],
        job_id=row["job_id"],
        status=row["status"],
        priority=row["priority"],
        claimed_by=row["claimed_by"],
        claimed_at=row["claimed_at"],
        created_at=row["created_at"],
        decision=row["decision"],
        confidence=row["confidence"],
        reasons=list(reasons),
        vision_signals=dict(vision),
        llm_signals=dict(llm),
        caption=row["caption"] or "",
        image_url=public_url(settings, row["object_key"]),
        content_type=row["content_type"],
    )


@router.get("/reviews", response_model=list[ReviewItemOut])
async def list_reviews(
    status: str | None = Query(default="pending"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ReviewItemOut]:
    settings = get_settings()
    async with connection() as conn:
        if status:
            rows = await conn.fetch(
                """
                SELECT rq.*, d.decision, d.confidence, d.reasons, d.vision_signals, d.llm_signals,
                       c.caption, c.object_key, c.content_type
                FROM review_queue rq
                JOIN decisions d ON d.id = rq.decision_id
                JOIN content_items c ON c.id = rq.content_id
                WHERE rq.status = $1
                ORDER BY rq.priority ASC, rq.created_at ASC
                LIMIT $2
                """,
                status,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT rq.*, d.decision, d.confidence, d.reasons, d.vision_signals, d.llm_signals,
                       c.caption, c.object_key, c.content_type
                FROM review_queue rq
                JOIN decisions d ON d.id = rq.decision_id
                JOIN content_items c ON c.id = rq.content_id
                ORDER BY rq.created_at DESC
                LIMIT $1
                """,
                limit,
            )
    return [_row_to_review(r, settings) for r in rows]


@router.post("/reviews/{review_id}/claim", response_model=ReviewItemOut)
async def claim_review(review_id: UUID, body: ClaimRequest) -> ReviewItemOut:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=CLAIM_TTL_MINUTES)
    async with connection() as conn:
        row = await conn.fetchrow(
            """
            UPDATE review_queue
            SET status = 'claimed',
                claimed_by = $2,
                claimed_at = $3,
                claim_expires_at = $4
            WHERE id = $1
              AND (
                status = 'pending'
                OR (status = 'claimed' AND claim_expires_at < $3)
                OR (status = 'claimed' AND claimed_by = $2)
              )
            RETURNING id
            """,
            review_id,
            body.reviewer,
            now,
            expires,
        )
        if not row:
            raise HTTPException(status_code=409, detail="Review not claimable")

        full = await conn.fetchrow(
            """
            SELECT rq.*, d.decision, d.confidence, d.reasons, d.vision_signals, d.llm_signals,
                   c.caption, c.object_key, c.content_type
            FROM review_queue rq
            JOIN decisions d ON d.id = rq.decision_id
            JOIN content_items c ON c.id = rq.content_id
            WHERE rq.id = $1
            """,
            review_id,
        )
        await conn.execute(
            """
            INSERT INTO audit_log (entity_type, entity_id, action, actor, detail)
            VALUES ('review', $1, 'claimed', $2, '{}'::jsonb)
            """,
            review_id,
            body.reviewer,
        )
    return _row_to_review(full, settings)


@router.post("/reviews/{review_id}/resolve", response_model=ReviewItemOut)
async def resolve_review(review_id: UUID, body: ResolveRequest) -> ReviewItemOut:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    status = "approved" if body.reviewer_decision == "ALLOW" else "rejected"

    async with connection() as conn:
        row = await conn.fetchrow(
            """
            UPDATE review_queue
            SET status = $2,
                reviewer_decision = $3,
                notes = $4,
                resolved_by = $5,
                resolved_at = $6
            WHERE id = $1
              AND status = 'claimed'
              AND claimed_by = $5
            RETURNING id
            """,
            review_id,
            status,
            body.reviewer_decision,
            body.notes,
            body.reviewer,
            now,
        )
        if not row:
            raise HTTPException(
                status_code=409,
                detail="Review must be claimed by this reviewer before resolve",
            )

        await conn.execute(
            """
            INSERT INTO audit_log (entity_type, entity_id, action, actor, detail)
            VALUES ('review', $1, 'resolved', $2, $3::jsonb)
            """,
            review_id,
            body.reviewer,
            json.dumps(
                {
                    "reviewer_decision": body.reviewer_decision,
                    "notes": body.notes,
                    "status": status,
                }
            ),
        )

        full = await conn.fetchrow(
            """
            SELECT rq.*, d.decision, d.confidence, d.reasons, d.vision_signals, d.llm_signals,
                   c.caption, c.object_key, c.content_type
            FROM review_queue rq
            JOIN decisions d ON d.id = rq.decision_id
            JOIN content_items c ON c.id = rq.content_id
            WHERE rq.id = $1
            """,
            review_id,
        )
    return _row_to_review(full, settings)


@router.get("/decisions", response_model=list[DecisionOut])
async def list_decisions(limit: int = Query(default=50, ge=1, le=200)) -> list[DecisionOut]:
    settings = get_settings()
    async with connection() as conn:
        rows = await conn.fetch(
            """
            SELECT d.*, c.caption, c.object_key
            FROM decisions d
            JOIN content_items c ON c.id = d.content_id
            ORDER BY d.created_at DESC
            LIMIT $1
            """,
            limit,
        )
    out: list[DecisionOut] = []
    for r in rows:
        reasons = r["reasons"]
        vision = r["vision_signals"]
        llm = r["llm_signals"]
        if isinstance(reasons, str):
            reasons = json.loads(reasons)
        if isinstance(vision, str):
            vision = json.loads(vision)
        if isinstance(llm, str):
            llm = json.loads(llm)
        out.append(
            DecisionOut(
                id=r["id"],
                job_id=r["job_id"],
                content_id=r["content_id"],
                content_hash=r["content_hash"],
                decision=r["decision"],
                confidence=r["confidence"],
                reasons=list(reasons),
                vision_signals=dict(vision),
                llm_signals=dict(llm),
                policy_version=r["policy_version"],
                pipeline_version=r["pipeline_version"],
                latency_ms=r["latency_ms"],
                needs_human_review=r["needs_human_review"],
                created_at=r["created_at"],
                caption=r["caption"],
                image_url=public_url(settings, r["object_key"]),
            )
        )
    return out


@router.get("/decisions/{decision_id}", response_model=DecisionOut)
async def get_decision(decision_id: UUID) -> DecisionOut:
    settings = get_settings()
    async with connection() as conn:
        r = await conn.fetchrow(
            """
            SELECT d.*, c.caption, c.object_key
            FROM decisions d
            JOIN content_items c ON c.id = d.content_id
            WHERE d.id = $1
            """,
            decision_id,
        )
    if not r:
        raise HTTPException(status_code=404, detail="Decision not found")
    reasons = r["reasons"]
    vision = r["vision_signals"]
    llm = r["llm_signals"]
    if isinstance(reasons, str):
        reasons = json.loads(reasons)
    if isinstance(vision, str):
        vision = json.loads(vision)
    if isinstance(llm, str):
        llm = json.loads(llm)
    return DecisionOut(
        id=r["id"],
        job_id=r["job_id"],
        content_id=r["content_id"],
        content_hash=r["content_hash"],
        decision=r["decision"],
        confidence=r["confidence"],
        reasons=list(reasons),
        vision_signals=dict(vision),
        llm_signals=dict(llm),
        policy_version=r["policy_version"],
        pipeline_version=r["pipeline_version"],
        latency_ms=r["latency_ms"],
        needs_human_review=r["needs_human_review"],
        created_at=r["created_at"],
        caption=r["caption"],
        image_url=public_url(settings, r["object_key"]),
    )
