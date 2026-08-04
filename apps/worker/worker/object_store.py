from __future__ import annotations

import boto3
from botocore.client import Config

from .config import Settings


def fetch_object(settings: Settings, object_key: str) -> tuple[bytes, str]:
    provider = settings.storage_provider.lower()
    if provider == "gcs":
        return _fetch_gcs(settings, object_key)
    return _fetch_s3(settings, object_key)


def _fetch_s3(settings: Settings, object_key: str) -> tuple[bytes, str]:
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4"),
    )
    resp = client.get_object(Bucket=settings.s3_bucket, Key=object_key)
    body = resp["Body"].read()
    content_type = resp.get("ContentType") or "application/octet-stream"
    return body, content_type


def _fetch_gcs(settings: Settings, object_key: str) -> tuple[bytes, str]:
    from google.cloud import storage  # type: ignore

    client_kwargs: dict = {}
    if settings.gcp_project:
        client_kwargs["project"] = settings.gcp_project
    if settings.google_application_credentials:
        from google.oauth2 import service_account  # type: ignore

        client_kwargs["credentials"] = service_account.Credentials.from_service_account_file(
            settings.google_application_credentials
        )
    client = storage.Client(**client_kwargs)
    blob = client.bucket(settings.gcs_bucket).blob(object_key)
    body = blob.download_as_bytes()
    content_type = blob.content_type or "application/octet-stream"
    return body, content_type
