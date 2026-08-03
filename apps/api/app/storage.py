from __future__ import annotations

import boto3
from botocore.client import Config

from .config import Settings


def s3_client(settings: Settings):
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4"),
    )


def put_object(
    *,
    settings: Settings,
    key: str,
    body: bytes,
    content_type: str,
) -> str:
    client = s3_client(settings)
    client.put_object(
        Bucket=settings.s3_bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
    )
    return key


def public_url(settings: Settings, key: str) -> str:
    base = settings.s3_public_endpoint_url.rstrip("/")
    return f"{base}/{settings.s3_bucket}/{key}"
