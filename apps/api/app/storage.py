from __future__ import annotations

from typing import Protocol

import boto3
from botocore.client import Config

from .config import Settings


class ObjectStorage(Protocol):
    def put(self, key: str, body: bytes, content_type: str) -> str: ...

    def public_url(self, key: str) -> str: ...


class S3ObjectStorage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4"),
        )

    def put(self, key: str, body: bytes, content_type: str) -> str:
        self._client.put_object(
            Bucket=self.settings.s3_bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
        return key

    def public_url(self, key: str) -> str:
        base = self.settings.s3_public_endpoint_url.rstrip("/")
        return f"{base}/{self.settings.s3_bucket}/{key}"


class GcsObjectStorage:
    """Google Cloud Storage adapter. Requires ADC / service account."""

    def __init__(self, settings: Settings):
        from google.cloud import storage  # type: ignore

        client_kwargs: dict = {}
        if settings.gcp_project:
            client_kwargs["project"] = settings.gcp_project
        if settings.google_application_credentials:
            from google.oauth2 import service_account  # type: ignore

            client_kwargs["credentials"] = service_account.Credentials.from_service_account_file(
                settings.google_application_credentials
            )
        self.settings = settings
        self._client = storage.Client(**client_kwargs)
        self._bucket = self._client.bucket(settings.gcs_bucket)

    def put(self, key: str, body: bytes, content_type: str) -> str:
        blob = self._bucket.blob(key)
        blob.upload_from_string(body, content_type=content_type)
        return key

    def public_url(self, key: str) -> str:
        return f"https://storage.googleapis.com/{self.settings.gcs_bucket}/{key}"


def get_storage(settings: Settings) -> ObjectStorage:
    provider = settings.storage_provider.lower()
    if provider == "gcs":
        return GcsObjectStorage(settings)
    return S3ObjectStorage(settings)


def put_object(
    *,
    settings: Settings,
    key: str,
    body: bytes,
    content_type: str,
) -> str:
    return get_storage(settings).put(key, body, content_type)


def public_url(settings: Settings, key: str) -> str:
    return get_storage(settings).public_url(key)
