from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import boto3

from .config import Settings


def sqs_client(settings: Settings):
    return boto3.client(
        "sqs",
        endpoint_url=settings.sqs_endpoint_url,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_default_region,
    )


def enqueue_job(
    *,
    settings: Settings,
    job_id: UUID,
    content_id: UUID,
    content_hash: str,
    object_key: str,
    caption: str,
) -> str:
    body: dict[str, Any] = {
        "job_id": str(job_id),
        "content_id": str(content_id),
        "content_hash": content_hash,
        "object_key": object_key,
        "caption": caption,
        "policy_version": settings.policy_version,
        "pipeline_version": settings.pipeline_version,
    }
    client = sqs_client(settings)
    resp = client.send_message(
        QueueUrl=settings.sqs_queue_url,
        MessageBody=json.dumps(body),
        MessageAttributes={
            "content_hash": {"DataType": "String", "StringValue": content_hash},
            "job_id": {"DataType": "String", "StringValue": str(job_id)},
        },
    )
    return resp["MessageId"]
