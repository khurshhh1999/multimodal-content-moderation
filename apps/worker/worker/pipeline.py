from __future__ import annotations

import json
import logging
import time
from uuid import UUID

import asyncpg
import boto3
from botocore.client import Config
from moderation_shared import DecisionEnvelope, ThresholdConfig, route_decision

from .adapters import get_llm_adapter, get_vision_adapter
from .config import Settings

logger = logging.getLogger(__name__)

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class ValidationError(Exception):
    pass


def _s3(settings: Settings):
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4"),
    )


def fetch_object(settings: Settings, object_key: str) -> tuple[bytes, str]:
    client = _s3(settings)
    resp = client.get_object(Bucket=settings.s3_bucket, Key=object_key)
    body = resp["Body"].read()
    content_type = resp.get("ContentType") or "application/octet-stream"
    return body, content_type


def validate(image_bytes: bytes, content_type: str, settings: Settings) -> list[str]:
    reasons: list[str] = []
    if content_type not in ALLOWED_TYPES:
        raise ValidationError(f"invalid_content_type:{content_type}")
    if len(image_bytes) == 0:
        raise ValidationError("empty_object")
    if len(image_bytes) > settings.max_upload_bytes:
        raise ValidationError("object_too_large")
    # Hook point for ClamAV / malware scan in Phase 4
    reasons.append("validation_ok")
    return reasons


async def process_job(
    *,
    conn: asyncpg.Connection,
    settings: Settings,
    job_id: UUID,
    content_id: UUID,
    content_hash: str,
    object_key: str,
    caption: str,
) -> DecisionEnvelope:
    started = time.perf_counter()

    # Idempotent: already decided?
    existing = await conn.fetchrow("SELECT id FROM decisions WHERE job_id = $1", job_id)
    if existing:
        logger.info("Job %s already has decision — skip", job_id)
        row = await conn.fetchrow(
            "SELECT envelope FROM decisions WHERE job_id = $1", job_id
        )
        return DecisionEnvelope.model_validate(json.loads(row["envelope"]) if isinstance(row["envelope"], str) else row["envelope"])

    await conn.execute(
        """
        UPDATE jobs
        SET status = 'processing', started_at = COALESCE(started_at, now()), attempts = attempts + 1
        WHERE id = $1
        """,
        job_id,
    )

    # Processing lock in Redis-equivalent via job status; content_hash unique on decisions via job

    image_bytes, content_type = fetch_object(settings, object_key)
    validate(image_bytes, content_type, settings)

    vision = get_vision_adapter(settings).analyze(image_bytes, caption)
    suggested, llm = get_llm_adapter(settings).classify(caption=caption, vision=vision)

    thresholds = ThresholdConfig(policy_version=settings.policy_version)
    final, needs_review, route_reasons = route_decision(
        suggested=suggested,
        confidence=llm.score,
        nsfw_score=vision.nsfw_score,
        violence_score=vision.violence_score,
        thresholds=thresholds,
    )

    reasons = list(route_reasons)
    if llm.rationale:
        reasons.append(llm.rationale)
    reasons.extend(vision.labels[:5])

    latency_ms = int((time.perf_counter() - started) * 1000)
    envelope = DecisionEnvelope(
        job_id=job_id,
        content_id=content_id,
        content_hash=content_hash,
        decision=final,
        confidence=round(float(llm.score), 4),
        reasons=reasons,
        vision=vision,
        llm=llm,
        policy_version=settings.policy_version,
        pipeline_version=settings.pipeline_version,
        latency_ms=latency_ms,
        needs_human_review=needs_review or final.value == "FLAG",
    )

    # Persist decision
    decision_id = await conn.fetchval(
        """
        INSERT INTO decisions (
          job_id, content_id, content_hash, decision, confidence, reasons,
          vision_signals, llm_signals, policy_version, pipeline_version,
          latency_ms, needs_human_review, envelope
        ) VALUES (
          $1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9,$10,$11,$12,$13::jsonb
        )
        ON CONFLICT (job_id) DO NOTHING
        RETURNING id
        """,
        job_id,
        content_id,
        content_hash,
        envelope.decision.value,
        envelope.confidence,
        json.dumps(envelope.reasons),
        envelope.vision.model_dump_json(),
        envelope.llm.model_dump_json(),
        envelope.policy_version,
        envelope.pipeline_version,
        envelope.latency_ms,
        envelope.needs_human_review,
        envelope.model_dump_json(),
    )

    if decision_id is None:
        # Concurrent insert won
        row = await conn.fetchrow("SELECT envelope FROM decisions WHERE job_id = $1", job_id)
        return DecisionEnvelope.model_validate(
            json.loads(row["envelope"]) if isinstance(row["envelope"], str) else row["envelope"]
        )

    await conn.execute(
        """
        UPDATE jobs SET status = 'succeeded', finished_at = now(), last_error = NULL
        WHERE id = $1
        """,
        job_id,
    )

    await conn.execute(
        """
        INSERT INTO audit_log (entity_type, entity_id, action, actor, detail)
        VALUES ('decision', $1, 'created', 'worker', $2::jsonb)
        """,
        decision_id,
        json.dumps(
            {
                "decision": envelope.decision.value,
                "confidence": envelope.confidence,
                "needs_human_review": envelope.needs_human_review,
            }
        ),
    )

    await conn.execute(
        """
        INSERT INTO metrics_events (event_type, payload)
        VALUES ('decision', $1::jsonb)
        """,
        json.dumps(
            {
                "decision": envelope.decision.value,
                "latency_ms": envelope.latency_ms,
                "auto": not envelope.needs_human_review,
            }
        ),
    )

    if envelope.needs_human_review:
        # Priority: higher risk → lower number
        priority = 10 if envelope.decision.value == "BLOCK" else 50
        if envelope.confidence < 0.6:
            priority = min(priority, 20)
        await conn.execute(
            """
            INSERT INTO review_queue (decision_id, content_id, job_id, status, priority)
            VALUES ($1, $2, $3, 'pending', $4)
            ON CONFLICT (decision_id) DO NOTHING
            """,
            decision_id,
            content_id,
            job_id,
            priority,
        )
        await conn.execute(
            """
            INSERT INTO audit_log (entity_type, entity_id, action, actor, detail)
            VALUES ('review', $1, 'enqueued', 'worker', '{}'::jsonb)
            """,
            decision_id,
        )

    return envelope
