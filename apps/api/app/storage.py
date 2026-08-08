from __future__ import annotations

from datetime import timedelta
from typing import Protocol

import boto3
from botocore.client import Config

from .config import Settings


class ObjectStorage(Protocol):
    def put(self, key: str, body: bytes, content_type: str) -> str: ...

    def signed_url(self, key: str, *, expires_in: int | None = None) -> str: ...


def _s3_client(settings: Settings, *, endpoint_url: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


class S3ObjectStorage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = _s3_client(settings, endpoint_url=settings.s3_endpoint_url)
        # Browser-facing URLs must use the public host (localhost), not the Docker DNS name.
        self._signer = _s3_client(settings, endpoint_url=settings.s3_public_endpoint_url)

    def put(self, key: str, body: bytes, content_type: str) -> str:
        self._client.put_object(
            Bucket=self.settings.s3_bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
        return key

    def signed_url(self, key: str, *, expires_in: int | None = None) -> str:
        ttl = expires_in if expires_in is not None else self.settings.signed_url_ttl_seconds
        return self._signer.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.settings.s3_bucket, "Key": key},
            ExpiresIn=max(1, int(ttl)),
        )


class GcsObjectStorage:
    """Google Cloud Storage adapter. Requires ADC / service account."""

    def __init__(self, settings: Settings):
        from google.cloud import storage  # type: ignore

        client_kwargs: dict = {}
        self._signing_credentials = None
        if settings.gcp_project:
            client_kwargs["project"] = settings.gcp_project
        if settings.google_application_credentials:
            from google.oauth2 import service_account  # type: ignore

            creds = service_account.Credentials.from_service_account_file(
                settings.google_application_credentials
            )
            client_kwargs["credentials"] = creds
            self._signing_credentials = creds
        self.settings = settings
        self._client = storage.Client(**client_kwargs)
        self._bucket = self._client.bucket(settings.gcs_bucket)

    def put(self, key: str, body: bytes, content_type: str) -> str:
        blob = self._bucket.blob(key)
        blob.upload_from_string(body, content_type=content_type)
        return key

    def signed_url(self, key: str, *, expires_in: int | None = None) -> str:
        ttl = expires_in if expires_in is not None else self.settings.signed_url_ttl_seconds
        blob = self._bucket.blob(key)
        kwargs: dict = {
            "version": "v4",
            "expiration": timedelta(seconds=max(1, int(ttl))),
            "method": "GET",
        }
        if self._signing_credentials is not None:
            kwargs["credentials"] = self._signing_credentials
        return blob.generate_signed_url(**kwargs)


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


def signed_url(settings: Settings, key: str, *, expires_in: int | None = None) -> str:
    return get_storage(settings).signed_url(key, expires_in=expires_in)


# Backward-compatible alias used by older call sites / docs.
def public_url(settings: Settings, key: str) -> str:
    return signed_url(settings, key)
