from __future__ import annotations

import json
import logging
import time
from uuid import UUID

import asyncpg
from moderation_shared import DecisionEnvelope, ThresholdConfig, route_decision

from .adapters import get_llm_adapter, get_vision_adapter
from .config import Settings
from .object_store import fetch_object

logger = logging.getLogger(__name__)

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class ValidationError(Exception):
    pass


def validate(image_bytes: bytes, content_type: str, settings: Settings) -> list[str]:
    reasons: list[str] = []
    if content_type not in ALLOWED_TYPES:
        raise ValidationError(f"invalid_content_type:{content_type}")
    if len(image_bytes) == 0:
        raise ValidationError("empty_object")
    if len(image_bytes) > settings.max_upload_bytes:
        raise ValidationError("object_too_large")
    # Hook point for ClamAV / malware scan
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

    thresholds = ThresholdConfig.from_values(
        policy_version=settings.policy_version,
        auto_allow=settings.auto_allow,
        auto_block=settings.auto_block,
        nsfw_block=settings.nsfw_block,
        nsfw_flag=settings.nsfw_flag,
        violence_block=settings.violence_block,
        violence_flag=settings.violence_flag,
    )
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
