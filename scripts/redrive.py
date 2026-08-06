#!/usr/bin/env python3
"""Redrive dead-letter queue messages back onto the main moderation jobs queue.

Moves SQS DLQ messages to the primary queue and resets matching Postgres jobs
(`dead` / `failed` → `queued`, attempts cleared) so the worker can retry.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

# Allow running from repo root without installing the worker package.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class DlqMessage:
    message_id: str
    receipt_handle: str
    body_raw: str
    job_id: str | None
    content_id: str | None
    content_hash: str | None


@dataclass(frozen=True)
class RedriveAction:
    message: DlqMessage
    reset_job: bool
    skip_reason: str | None = None


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def parse_dlq_message(raw: dict[str, Any]) -> DlqMessage:
    body_raw = raw.get("Body") or ""
    job_id = content_id = content_hash = None
    try:
        body = json.loads(body_raw)
        if isinstance(body, dict):
            job_id = body.get("job_id")
            content_id = body.get("content_id")
            content_hash = body.get("content_hash")
    except json.JSONDecodeError:
        pass

    attrs = raw.get("MessageAttributes") or {}
    if not job_id and "job_id" in attrs:
        job_id = attrs["job_id"].get("StringValue")
    if not content_hash and "content_hash" in attrs:
        content_hash = attrs["content_hash"].get("StringValue")

    return DlqMessage(
        message_id=raw.get("MessageId") or "",
        receipt_handle=raw.get("ReceiptHandle") or "",
        body_raw=body_raw,
        job_id=str(job_id) if job_id else None,
        content_id=str(content_id) if content_id else None,
        content_hash=str(content_hash) if content_hash else None,
    )


def plan_redrive(
    message: DlqMessage,
    *,
    job_id_filter: str | None = None,
    job_status: str | None = None,
) -> RedriveAction:
    """Decide whether to move a DLQ message and reset its job row."""
    if not message.body_raw:
        return RedriveAction(message, reset_job=False, skip_reason="empty_body")
    if not message.job_id:
        return RedriveAction(message, reset_job=False, skip_reason="missing_job_id")
    try:
        UUID(message.job_id)
    except ValueError:
        return RedriveAction(message, reset_job=False, skip_reason="invalid_job_id")

    if job_id_filter and message.job_id != job_id_filter:
        return RedriveAction(message, reset_job=False, skip_reason="job_id_filter")

    if job_status is None:
        # Queue-only redrive; DB lookup deferred / unavailable.
        return RedriveAction(message, reset_job=True)

    if job_status in ("dead", "failed"):
        return RedriveAction(message, reset_job=True)
    if job_status == "succeeded":
        return RedriveAction(message, reset_job=False, skip_reason="job_already_succeeded")
    if job_status in ("queued", "processing"):
        # Still move the message so a stuck/orphaned DLQ copy can be consumed;
        # do not clobber in-flight attempt counters.
        return RedriveAction(message, reset_job=False, skip_reason=f"job_status_{job_status}")
    return RedriveAction(message, reset_job=False, skip_reason=f"unknown_status_{job_status}")


def _sqs_client():
    import boto3

    return boto3.client(
        "sqs",
        endpoint_url=env("SQS_ENDPOINT_URL", "http://localhost:4566"),
        aws_access_key_id=env("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=env("AWS_SECRET_ACCESS_KEY", "test"),
        region_name=env("AWS_DEFAULT_REGION", "us-east-1"),
    )


def queue_stats(client, queue_url: str) -> dict[str, int]:
    resp = client.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed",
        ],
    )
    attrs = resp.get("Attributes") or {}
    return {
        "available": int(attrs.get("ApproximateNumberOfMessages", "0")),
        "in_flight": int(attrs.get("ApproximateNumberOfMessagesNotVisible", "0")),
        "delayed": int(attrs.get("ApproximateNumberOfMessagesDelayed", "0")),
    }


async def fetch_job_status(conn, job_id: str) -> str | None:
    return await conn.fetchval("SELECT status FROM jobs WHERE id = $1::uuid", job_id)


async def reset_job_for_redrive(conn, job_id: str) -> bool:
    row = await conn.fetchrow(
        """
        UPDATE jobs
        SET status = 'queued',
            attempts = 0,
            last_error = NULL,
            started_at = NULL,
            finished_at = NULL,
            enqueued_at = now()
        WHERE id = $1::uuid
          AND status IN ('dead', 'failed')
        RETURNING id
        """,
        job_id,
    )
    if not row:
        return False
    await conn.execute(
        """
        INSERT INTO audit_log (entity_type, entity_id, action, actor, detail)
        VALUES ('job', $1::uuid, 'redrive', 'redrive-cli', $2::jsonb)
        """,
        job_id,
        json.dumps({"source": "dlq", "to": "main_queue"}),
    )
    return True


async def redrive(
    *,
    limit: int,
    dry_run: bool,
    job_id_filter: str | None,
    skip_db: bool,
) -> int:
    queue_provider = env("QUEUE_PROVIDER", "sqs").lower()
    if queue_provider != "sqs":
        print(
            f"error: redrive CLI currently supports QUEUE_PROVIDER=sqs "
            f"(got {queue_provider!r})",
            file=sys.stderr,
        )
        return 2

    main_url = env("SQS_QUEUE_URL", "http://localhost:4566/000000000000/moderation-jobs")
    dlq_url = env("SQS_DLQ_URL", "http://localhost:4566/000000000000/moderation-jobs-dlq")
    client = _sqs_client()

    main_stats = queue_stats(client, main_url)
    dlq_stats = queue_stats(client, dlq_url)
    print(
        f"queues: main available={main_stats['available']} "
        f"in_flight={main_stats['in_flight']} | "
        f"dlq available={dlq_stats['available']} "
        f"in_flight={dlq_stats['in_flight']}"
    )

    conn = None
    if not skip_db:
        import asyncpg

        database_url = env(
            "DATABASE_URL",
            "postgresql://moderation:moderation@localhost:5432/moderation",
        )
        try:
            conn = await asyncpg.connect(dsn=database_url)
        except Exception as exc:  # noqa: BLE001
            print(f"error: cannot connect to Postgres ({exc})", file=sys.stderr)
            print("hint: pass --skip-db to redrive queue messages only", file=sys.stderr)
            return 2

    moved = skipped = reset = 0
    remaining = limit
    visibility = 5 if dry_run else 60

    try:
        while remaining > 0:
            batch = min(10, remaining)
            resp = await asyncio.to_thread(
                client.receive_message,
                QueueUrl=dlq_url,
                MaxNumberOfMessages=batch,
                WaitTimeSeconds=1,
                VisibilityTimeout=visibility,
                MessageAttributeNames=["All"],
            )
            messages = resp.get("Messages") or []
            if not messages:
                break

            for raw in messages:
                remaining -= 1
                msg = parse_dlq_message(raw)
                status: str | None = None
                if conn and msg.job_id:
                    status = await fetch_job_status(conn, msg.job_id)
                    if status is None:
                        print(f"warn: no job row for {msg.job_id} (queue-only redrive)")

                action = plan_redrive(msg, job_id_filter=job_id_filter, job_status=status)
                if action.skip_reason in ("job_id_filter", "empty_body", "missing_job_id", "invalid_job_id"):
                    skipped += 1
                    print(
                        f"skip message_id={msg.message_id} reason={action.skip_reason} "
                        f"job_id={msg.job_id}"
                    )
                    continue
                if action.skip_reason == "job_already_succeeded":
                    skipped += 1
                    print(
                        f"skip message_id={msg.message_id} reason={action.skip_reason} "
                        f"job_id={msg.job_id} (leaving in DLQ)"
                    )
                    continue

                label = "dry-run would redrive" if dry_run else "redrive"
                print(
                    f"{label} message_id={msg.message_id} job_id={msg.job_id} "
                    f"content_hash={(msg.content_hash or '')[:12]}... "
                    f"reset_job={action.reset_job}"
                    + (f" note={action.skip_reason}" if action.skip_reason else "")
                )

                if dry_run:
                    moved += 1
                    if action.reset_job:
                        reset += 1
                    continue

                # Reset DB before enqueue so the worker does not see a dead row.
                if action.reset_job and conn and msg.job_id:
                    if await reset_job_for_redrive(conn, msg.job_id):
                        reset += 1
                    else:
                        print(f"warn: job {msg.job_id} not reset (status may have changed)")

                attrs: dict[str, dict[str, str]] = {}
                if msg.content_hash:
                    attrs["content_hash"] = {
                        "DataType": "String",
                        "StringValue": msg.content_hash,
                    }
                if msg.job_id:
                    attrs["job_id"] = {
                        "DataType": "String",
                        "StringValue": msg.job_id,
                    }
                send_kwargs: dict[str, Any] = {
                    "QueueUrl": main_url,
                    "MessageBody": msg.body_raw,
                }
                if attrs:
                    send_kwargs["MessageAttributes"] = attrs
                await asyncio.to_thread(client.send_message, **send_kwargs)
                await asyncio.to_thread(
                    client.delete_message,
                    QueueUrl=dlq_url,
                    ReceiptHandle=msg.receipt_handle,
                )
                moved += 1
    finally:
        if conn is not None:
            await conn.close()

    mode = "dry-run " if dry_run else ""
    print(f"done: {mode}moved={moved} jobs_reset={reset} skipped={skipped}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Redrive moderation jobs from the SQS dead-letter queue",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max DLQ messages to process (default: 50)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect / plan only; do not move messages or reset jobs",
    )
    p.add_argument(
        "--job-id",
        dest="job_id",
        default=None,
        help="Only redrive messages for this job UUID",
    )
    p.add_argument(
        "--stats",
        action="store_true",
        help="Print main/DLQ depths and exit",
    )
    p.add_argument(
        "--skip-db",
        action="store_true",
        help="Move queue messages without resetting Postgres job rows",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    _load_dotenv(ROOT / ".env")
    args = build_parser().parse_args(argv)

    if args.limit < 1:
        print("error: --limit must be >= 1", file=sys.stderr)
        return 2

    if args.stats:
        queue_provider = env("QUEUE_PROVIDER", "sqs").lower()
        if queue_provider != "sqs":
            print(f"error: --stats requires QUEUE_PROVIDER=sqs", file=sys.stderr)
            return 2
        client = _sqs_client()
        main_url = env("SQS_QUEUE_URL", "http://localhost:4566/000000000000/moderation-jobs")
        dlq_url = env("SQS_DLQ_URL", "http://localhost:4566/000000000000/moderation-jobs-dlq")
        main_stats = queue_stats(client, main_url)
        dlq_stats = queue_stats(client, dlq_url)
        print(json.dumps({"main": main_stats, "dlq": dlq_stats}, indent=2))
        return 0

    return asyncio.run(
        redrive(
            limit=args.limit,
            dry_run=args.dry_run,
            job_id_filter=args.job_id,
            skip_db=args.skip_db,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
