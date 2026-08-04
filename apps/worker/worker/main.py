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
            raise
        except Exception as exc:  # noqa: BLE001
            await mark_failed(conn, job_id, str(exc))
            logger.exception("Job failed job=%s", job_id)
            raise


async def poll_sqs(pool: asyncpg.Pool, settings) -> None:
    client = _sqs(settings)
    logger.info(
        "Worker polling SQS %s vision=%s llm=%s",
        settings.sqs_queue_url,
        settings.vision_provider,
        settings.llm_provider,
    )

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
                await asyncio.to_thread(
                    client.delete_message,
                    QueueUrl=settings.sqs_queue_url,
                    ReceiptHandle=receipt,
                )
            except Exception:
                logger.error("Leaving message for retry/DLQ")


async def poll_pubsub(pool: asyncpg.Pool, settings) -> None:
    from google.cloud import pubsub_v1  # type: ignore

    subscriber_kwargs: dict = {}
    if settings.google_application_credentials:
        from google.oauth2 import service_account  # type: ignore

        subscriber_kwargs["credentials"] = service_account.Credentials.from_service_account_file(
            settings.google_application_credentials
        )
    subscriber = pubsub_v1.SubscriberClient(**subscriber_kwargs)
    if not settings.gcp_project:
        raise RuntimeError("GCP_PROJECT is required when QUEUE_PROVIDER=pubsub")
    sub_path = subscriber.subscription_path(settings.gcp_project, settings.pubsub_subscription)
    logger.info(
        "Worker polling Pub/Sub %s vision=%s llm=%s",
        sub_path,
        settings.vision_provider,
        settings.llm_provider,
    )

    while not _shutdown.is_set():
        try:
            resp = await asyncio.to_thread(
                subscriber.pull,
                request={
                    "subscription": sub_path,
                    "max_messages": 5,
                    "return_immediately": False,
                },
                timeout=settings.poll_wait_seconds + 5,
            )
        except Exception as exc:  # noqa: BLE001
            # DeadlineExceeded is normal idle behavior for some clients
            if "DeadlineExceeded" in type(exc).__name__ or "Deadline Exceeded" in str(exc):
                continue
            logger.error("Pub/Sub pull error: %s", exc)
            await asyncio.sleep(2)
            continue

        received = list(resp.received_messages or [])
        if not received:
            continue

        ack_ids: list[str] = []
        nack_ids: list[str] = []
        for msg in received:
            try:
                body = json.loads(msg.message.data.decode("utf-8"))
                await handle_message(pool, settings, body)
                ack_ids.append(msg.ack_id)
            except ValidationError:
                ack_ids.append(msg.ack_id)
            except Exception:
                nack_ids.append(msg.ack_id)
                logger.error("Nacking Pub/Sub message for retry")

        if ack_ids:
            await asyncio.to_thread(
                subscriber.acknowledge,
                request={"subscription": sub_path, "ack_ids": ack_ids},
            )
        if nack_ids:
            await asyncio.to_thread(
                subscriber.modify_ack_deadline,
                request={
                    "subscription": sub_path,
                    "ack_ids": nack_ids,
                    "ack_deadline_seconds": 0,
                },
            )


async def poll_loop(pool: asyncpg.Pool) -> None:
    settings = get_settings()
    if settings.queue_provider.lower() == "pubsub":
        await poll_pubsub(pool, settings)
    else:
        await poll_sqs(pool, settings)


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
