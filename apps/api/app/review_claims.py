from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

import asyncpg

from .redis_client import CLAIM_TTL_SECONDS

RELEASE_EXPIRED_CLAIMS_SQL = """
WITH expired AS (
  SELECT id, claimed_by
  FROM review_queue
  WHERE status = 'claimed'
    AND (claim_expires_at IS NULL OR claim_expires_at < $1)
)
UPDATE review_queue AS rq
SET status = 'pending',
    claimed_by = NULL,
    claimed_at = NULL,
    claim_expires_at = NULL
FROM expired
WHERE rq.id = expired.id
RETURNING rq.id, expired.claimed_by
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_claim_expired(
    *,
    status: str,
    claim_expires_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    """True when a claimed row should return to the pending queue."""
    if status != "claimed":
        return False
    if claim_expires_at is None:
        return True
    current = now or utcnow()
    if claim_expires_at.tzinfo is None:
        claim_expires_at = claim_expires_at.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return claim_expires_at <= current


def claim_is_active(
    *,
    status: str,
    claim_expires_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    return status == "claimed" and not is_claim_expired(
        status=status,
        claim_expires_at=claim_expires_at,
        now=now,
    )


async def release_expired_claims(
    conn: asyncpg.Connection,
    *,
    now: datetime | None = None,
) -> list[tuple[UUID, str | None]]:
    """Return expired claimed rows to pending and audit each release.

    Redis claim keys expire on their own TTL; this keeps Postgres in sync so
    list/metrics endpoints do not hide work behind a dead lock.
    """
    current = now or utcnow()
    rows = await conn.fetch(RELEASE_EXPIRED_CLAIMS_SQL, current)
    released: list[tuple[UUID, str | None]] = []
    for row in rows:
        review_id = row["id"]
        actor = row["claimed_by"] or "system"
        released.append((review_id, row["claimed_by"]))
        await conn.execute(
            """
            INSERT INTO audit_log (entity_type, entity_id, action, actor, detail)
            VALUES ('review', $1, 'claim_expired', $2, $3::jsonb)
            """,
            review_id,
            actor,
            json.dumps({"claim_ttl_seconds": CLAIM_TTL_SECONDS}),
        )
    return released
