from __future__ import annotations

import json
import logging
import time
from uuid import UUID

import asyncpg
from moderation_shared import DecisionEnvelope, ThresholdConfig, route_decision
from opentelemetry.trace import SpanKind

from .adapters import get_llm_adapter, get_vision_adapter
from .config import Settings
from .object_store import fetch_object
from .telemetry import start_span

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


def review_priority(decision: str, confidence: float) -> int:
    """Lower number = higher urgency in the human queue."""
    priority = 10 if decision == "BLOCK" else 50
    if confidence < 0.6:
        priority = min(priority, 20)
    return priority


def _parse_envelope(raw: object) -> DecisionEnvelope:
    if isinstance(raw, str):
        raw = json.loads(raw)
    return DecisionEnvelope.model_validate(raw)


async def persist_envelope(
    conn: asyncpg.Connection,
    envelope: DecisionEnvelope,
) -> DecisionEnvelope:
    """Write decision + job success + optional review row atomically."""
    async with conn.transaction():
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
            envelope.job_id,
            envelope.content_id,
            envelope.content_hash,
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
            row = await conn.fetchrow(
                "SELECT envelope FROM decisions WHERE job_id = $1",
                envelope.job_id,
            )
            return _parse_envelope(row["envelope"])

        await conn.execute(
            """
            UPDATE jobs SET status = 'succeeded', finished_at = now(), last_error = NULL
            WHERE id = $1
            """,
            envelope.job_id,
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
            await conn.execute(
                """
                INSERT INTO review_queue (decision_id, content_id, job_id, status, priority)
                VALUES ($1, $2, $3, 'pending', $4)
                ON CONFLICT (decision_id) DO NOTHING
                """,
                decision_id,
                envelope.content_id,
                envelope.job_id,
                review_priority(envelope.decision.value, envelope.confidence),
            )
            await conn.execute(
                """
                INSERT INTO audit_log (entity_type, entity_id, action, actor, detail)
                VALUES ('review', $1, 'enqueued', 'worker', '{}'::jsonb)
                """,
                decision_id,
            )

    return envelope


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

    with start_span(
        "pipeline.process_job",
        kind=SpanKind.INTERNAL,
        attributes={
            "moderation.job_id": str(job_id),
            "moderation.content_id": str(content_id),
            "moderation.content_hash": content_hash,
            "moderation.object_key": object_key,
            "moderation.policy_version": settings.policy_version,
            "moderation.vision_provider": settings.vision_provider,
            "moderation.llm_provider": settings.llm_provider,
        },
    ) as root_span:
        # Idempotent: already decided?
        existing = await conn.fetchrow("SELECT id FROM decisions WHERE job_id = $1", job_id)
        if existing:
            logger.info("Job %s already has decision — skip", job_id)
            row = await conn.fetchrow(
                "SELECT envelope FROM decisions WHERE job_id = $1", job_id
            )
            envelope = _parse_envelope(row["envelope"])
            root_span.set_attribute("moderation.decision", envelope.decision.value)
            root_span.set_attribute("moderation.deduplicated", True)
            return envelope

        await conn.execute(
            """
            UPDATE jobs
            SET status = 'processing', started_at = COALESCE(started_at, now()), attempts = attempts + 1
            WHERE id = $1
            """,
            job_id,
        )

        with start_span("pipeline.fetch_object"):
            image_bytes, content_type = fetch_object(settings, object_key)

        with start_span(
            "pipeline.validate",
            attributes={"moderation.content_type": content_type, "moderation.byte_size": len(image_bytes)},
        ):
            validate(image_bytes, content_type, settings)

        with start_span(
            "pipeline.vision",
            attributes={"moderation.vision_provider": settings.vision_provider},
        ):
            vision = get_vision_adapter(settings).analyze(image_bytes, caption)

        with start_span(
            "pipeline.llm",
            attributes={"moderation.llm_provider": settings.llm_provider},
        ):
            suggested, llm = get_llm_adapter(settings).classify(caption=caption, vision=vision)

        with start_span("pipeline.route"):
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

        root_span.set_attribute("moderation.decision", envelope.decision.value)
        root_span.set_attribute("moderation.confidence", envelope.confidence)
        root_span.set_attribute("moderation.needs_human_review", envelope.needs_human_review)
        root_span.set_attribute("moderation.latency_ms", envelope.latency_ms)

        with start_span("pipeline.persist_decision"):
            stored = await persist_envelope(conn, envelope)
            if stored is not envelope:
                return stored

        return envelope
