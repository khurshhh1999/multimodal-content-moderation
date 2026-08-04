from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import boto3

from .config import Settings


def _job_body(
    *,
    job_id: UUID,
    content_id: UUID,
    content_hash: str,
    object_key: str,
    caption: str,
    settings: Settings,
) -> dict[str, Any]:
    return {
        "job_id": str(job_id),
        "content_id": str(content_id),
        "content_hash": content_hash,
        "object_key": object_key,
        "caption": caption,
        "policy_version": settings.policy_version,
        "pipeline_version": settings.pipeline_version,
    }


def enqueue_sqs(
    *,
    settings: Settings,
    job_id: UUID,
    content_id: UUID,
    content_hash: str,
    object_key: str,
    caption: str,
) -> str:
    body = _job_body(
        job_id=job_id,
        content_id=content_id,
        content_hash=content_hash,
        object_key=object_key,
        caption=caption,
        settings=settings,
    )
    client = boto3.client(
        "sqs",
        endpoint_url=settings.sqs_endpoint_url,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_default_region,
    )
    resp = client.send_message(
        QueueUrl=settings.sqs_queue_url,
        MessageBody=json.dumps(body),
        MessageAttributes={
            "content_hash": {"DataType": "String", "StringValue": content_hash},
            "job_id": {"DataType": "String", "StringValue": str(job_id)},
        },
    )
    return resp["MessageId"]


def enqueue_pubsub(
    *,
    settings: Settings,
    job_id: UUID,
    content_id: UUID,
    content_hash: str,
    object_key: str,
    caption: str,
) -> str:
    from google.cloud import pubsub_v1  # type: ignore

    body = _job_body(
        job_id=job_id,
        content_id=content_id,
        content_hash=content_hash,
        object_key=object_key,
        caption=caption,
        settings=settings,
    )
    if not settings.gcp_project:
        raise RuntimeError("GCP_PROJECT is required when QUEUE_PROVIDER=pubsub")
    client_kwargs: dict = {}
    if settings.google_application_credentials:
        from google.oauth2 import service_account  # type: ignore

        client_kwargs["credentials"] = service_account.Credentials.from_service_account_file(
            settings.google_application_credentials
        )
    publisher = pubsub_v1.PublisherClient(**client_kwargs)
    topic_path = publisher.topic_path(settings.gcp_project, settings.pubsub_topic)
    future = publisher.publish(
        topic_path,
        json.dumps(body).encode("utf-8"),
        content_hash=content_hash,
        job_id=str(job_id),
    )
    return future.result(timeout=30)


def enqueue_job(
    *,
    settings: Settings,
    job_id: UUID,
    content_id: UUID,
    content_hash: str,
    object_key: str,
    caption: str,
) -> str:
    provider = settings.queue_provider.lower()
    if provider == "pubsub":
        return enqueue_pubsub(
            settings=settings,
            job_id=job_id,
            content_id=content_id,
            content_hash=content_hash,
            object_key=object_key,
            caption=caption,
        )
    return enqueue_sqs(
        settings=settings,
        job_id=job_id,
        content_id=content_id,
        content_hash=content_hash,
        object_key=object_key,
        caption=caption,
    )
