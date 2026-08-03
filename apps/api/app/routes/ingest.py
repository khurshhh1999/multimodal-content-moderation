from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..config import get_settings
from ..db import connection
from ..hashing import content_hash
from ..queue import enqueue_job
from ..redis_client import acquire_ingest_lock
from ..schemas import IngestResponse
from ..storage import put_object

router = APIRouter(prefix="/v1", tags=["ingest"])


@router.post("/content", response_model=IngestResponse)
async def ingest_content(
    image: Annotated[UploadFile, File(...)],
    caption: Annotated[str, Form()] = "",
) -> IngestResponse:
    settings = get_settings()
    allowed = {c.strip() for c in settings.allowed_content_types.split(",") if c.strip()}

    content_type = image.content_type or "application/octet-stream"
    if content_type not in allowed:
        raise HTTPException(status_code=415, detail=f"Unsupported content type: {content_type}")

    body = await image.read()
    if not body:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(body) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="File exceeds max upload size")

    digest = content_hash(body, caption, settings.policy_version)

    async with connection() as conn:
        existing = await conn.fetchrow(
            """
            SELECT j.id AS job_id, c.id AS content_id, j.status
            FROM jobs j
            JOIN content_items c ON c.id = j.content_id
            WHERE j.content_hash = $1
            """,
            digest,
        )
        if existing:
            return IngestResponse(
                job_id=existing["job_id"],
                content_id=existing["content_id"],
                content_hash=digest,
                status=existing["status"],
                deduplicated=True,
                message="idempotent hit — existing job returned",
            )

    got_lock = await acquire_ingest_lock(digest)
    if not got_lock:
        # Another request is writing the same hash; wait briefly via DB uniqueness
        async with connection() as conn:
            for _ in range(20):
                row = await conn.fetchrow(
                    "SELECT id AS job_id, content_id, status FROM jobs WHERE content_hash = $1",
                    digest,
                )
                if row:
                    return IngestResponse(
                        job_id=row["job_id"],
                        content_id=row["content_id"],
                        content_hash=digest,
                        status=row["status"],
                        deduplicated=True,
                        message="idempotent hit — concurrent ingest",
                    )
                import asyncio

                await asyncio.sleep(0.05)
        raise HTTPException(status_code=409, detail="Concurrent ingest in progress; retry")

    content_id = uuid.uuid4()
    job_id = uuid.uuid4()
    object_key = f"content/{digest[:2]}/{digest}/{content_id}.{_ext(content_type)}"

    try:
        put_object(
            settings=settings,
            key=object_key,
            body=body,
            content_type=content_type,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Object storage failed: {exc}") from exc

    async with connection() as conn:
        async with conn.transaction():
            try:
                await conn.execute(
                    """
                    INSERT INTO content_items
                      (id, content_hash, object_key, bucket, caption, content_type, byte_size, source)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, 'api')
                    """,
                    content_id,
                    digest,
                    object_key,
                    settings.s3_bucket,
                    caption.strip(),
                    content_type,
                    len(body),
                )
                await conn.execute(
                    """
                    INSERT INTO jobs (id, content_id, content_hash, status)
                    VALUES ($1, $2, $3, 'queued')
                    """,
                    job_id,
                    content_id,
                    digest,
                )
                await conn.execute(
                    """
                    INSERT INTO audit_log (entity_type, entity_id, action, actor, detail)
                    VALUES ('job', $1, 'enqueued', 'api', $2::jsonb)
                    """,
                    job_id,
                    f'{{"content_hash":"{digest}"}}',
                )
            except Exception as exc:  # noqa: BLE001
                # Unique violation race
                if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                    row = await conn.fetchrow(
                        "SELECT id AS job_id, content_id, status FROM jobs WHERE content_hash = $1",
                        digest,
                    )
                    if row:
                        return IngestResponse(
                            job_id=row["job_id"],
                            content_id=row["content_id"],
                            content_hash=digest,
                            status=row["status"],
                            deduplicated=True,
                            message="idempotent hit — race resolved",
                        )
                raise

    try:
        enqueue_job(
            settings=settings,
            job_id=job_id,
            content_id=content_id,
            content_hash=digest,
            object_key=object_key,
            caption=caption.strip(),
        )
    except Exception as exc:  # noqa: BLE001
        async with connection() as conn:
            await conn.execute(
                "UPDATE jobs SET status = 'failed', last_error = $2 WHERE id = $1",
                job_id,
                f"enqueue failed: {exc}",
            )
        raise HTTPException(status_code=502, detail=f"Queue enqueue failed: {exc}") from exc

    return IngestResponse(
        job_id=job_id,
        content_id=content_id,
        content_hash=digest,
        status="queued",
        deduplicated=False,
        message="accepted",
    )


def _ext(content_type: str) -> str:
    return {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
    }.get(content_type, "bin")
