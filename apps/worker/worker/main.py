from __future__ import annotations

import asyncio
import json
import logging
import signal
from uuid import UUID

import asyncpg
import boto3

from .config import get_settings
from .pipeline import ValidationError, process_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [worker] %(message)s",
)
logger = logging.getLogger("worker")

_shutdown = asyncio.Event()


def _sqs(settings):
    return boto3.client(
        "sqs",
        endpoint_url=settings.sqs_endpoint_url,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_default_region,
    )


async def mark_failed(conn: asyncpg.Connection, job_id: UUID, error: str) -> None:
    row = await conn.fetchrow("SELECT attempts, max_attempts FROM jobs WHERE id = $1", job_id)
    if not row:
        return
    status = "dead" if row["attempts"] >= row["max_attempts"] else "failed"
    await conn.execute(
        "UPDATE jobs SET status = $2, last_error = $3, finished_at = now() WHERE id = $1",
        job_id,
        status,
        error[:2000],
    )
    await conn.execute(
        """
        INSERT INTO audit_log (entity_type, entity_id, action, actor, detail)
        VALUES ('job', $1, $2, 'worker', $3::jsonb)
        """,
        job_id,
        status,
        json.dumps({"error": error[:500]}),
    )


async def handle_message(pool: asyncpg.Pool, settings, body: dict) -> None:
    job_id = UUID(body["job_id"])
    content_id = UUID(body["content_id"])
    content_hash = body["content_hash"]
    object_key = body["object_key"]
    caption = body.get("caption") or ""

    # Redis-style idempotency: skip if job already succeeded
    async with pool.acquire() as conn:
        status = await conn.fetchval("SELECT status FROM jobs WHERE id = $1", job_id)
        if status == "succeeded":
            logger.info("Skip already-succeeded job %s", job_id)
            return
        try:
            envelope = await process_job(
                conn=conn,
                settings=settings,
                job_id=job_id,
                content_id=content_id,
                content_hash=content_hash,
                object_key=object_key,
                caption=caption,
            )
            logger.info(
                "Processed job=%s decision=%s conf=%.2f review=%s latency=%dms",
                job_id,
                envelope.decision.value,
                envelope.confidence,
                envelope.needs_human_review,
                envelope.latency_ms,
            )
        except ValidationError as exc:
            await mark_failed(conn, job_id, f"validation: {exc}")
            logger.warning("Validation failed job=%s err=%s", job_id, exc)
            # Delete message (no retry for bad payload types) — caller deletes
            raise
        except Exception as exc:  # noqa: BLE001
            await mark_failed(conn, job_id, str(exc))
            logger.exception("Job failed job=%s", job_id)
            raise


async def poll_loop(pool: asyncpg.Pool) -> None:
    settings = get_settings()
    client = _sqs(settings)
    logger.info("Worker polling %s provider=%s/%s", settings.sqs_queue_url, settings.vision_provider, settings.llm_provider)

    while not _shutdown.is_set():
        try:
            resp = await asyncio.to_thread(
                client.receive_message,
                QueueUrl=settings.sqs_queue_url,
                MaxNumberOfMessages=5,
                WaitTimeSeconds=settings.poll_wait_seconds,
                VisibilityTimeout=settings.visibility_timeout,
                MessageAttributeNames=["All"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("SQS receive error: %s", exc)
            await asyncio.sleep(2)
            continue

        messages = resp.get("Messages") or []
        if not messages:
            continue

        for msg in messages:
            receipt = msg["ReceiptHandle"]
            try:
                body = json.loads(msg["Body"])
                await handle_message(pool, settings, body)
                await asyncio.to_thread(
                    client.delete_message,
                    QueueUrl=settings.sqs_queue_url,
                    ReceiptHandle=receipt,
                )
            except ValidationError:
                # Poison for validation — delete so it doesn't loop forever;
                # DLQ still catches processing failures via receive count.
                await asyncio.to_thread(
                    client.delete_message,
                    QueueUrl=settings.sqs_queue_url,
                    ReceiptHandle=receipt,
                )
            except Exception:
                # Leave message for retry / DLQ via RedrivePolicy
                logger.error("Leaving message for retry/DLQ")


async def main() -> None:
    settings = get_settings()
    pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=5)

    loop = asyncio.get_running_loop()

    def _stop() -> None:
        logger.info("Shutdown signal received")
        _shutdown.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass

    try:
        await poll_loop(pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
