from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.storage import GcsObjectStorage, S3ObjectStorage, public_url, signed_url


def _s3_settings(**overrides) -> Settings:
    data = {
        "storage_provider": "s3",
        "s3_endpoint_url": "http://minio:9000",
        "s3_public_endpoint_url": "http://localhost:9000",
        "s3_access_key": "minioadmin",
        "s3_secret_key": "minioadmin",
        "s3_bucket": "moderation-content",
        "s3_region": "us-east-1",
        "signed_url_ttl_seconds": 900,
    }
    data.update(overrides)
    return Settings(**data)


@patch("app.storage.boto3.client")
def test_s3_signed_url_uses_public_endpoint_and_ttl(mock_client_factory):
    put_client = MagicMock()
    sign_client = MagicMock()
    sign_client.generate_presigned_url.return_value = (
        "http://localhost:9000/moderation-content/k?"
        "X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=900"
    )
    mock_client_factory.side_effect = [put_client, sign_client]

    store = S3ObjectStorage(_s3_settings())
    url = store.signed_url("content/abc.jpg")

    assert "X-Amz-" in url
    assert url.startswith("http://localhost:9000/")
    assert mock_client_factory.call_count == 2
    endpoints = [c.kwargs["endpoint_url"] for c in mock_client_factory.call_args_list]
    assert endpoints == ["http://minio:9000", "http://localhost:9000"]
    sign_client.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "moderation-content", "Key": "content/abc.jpg"},
        ExpiresIn=900,
    )


@patch("app.storage.boto3.client")
def test_s3_signed_url_honors_explicit_expires(mock_client_factory):
    put_client = MagicMock()
    sign_client = MagicMock()
    sign_client.generate_presigned_url.return_value = "http://localhost:9000/x?sig=1"
    mock_client_factory.side_effect = [put_client, sign_client]

    store = S3ObjectStorage(_s3_settings(signed_url_ttl_seconds=60))
    store.signed_url("k", expires_in=120)

    assert sign_client.generate_presigned_url.call_args.kwargs["ExpiresIn"] == 120


@patch("app.storage.boto3.client")
def test_public_url_alias_returns_signed_url(mock_client_factory):
    put_client = MagicMock()
    sign_client = MagicMock()
    sign_client.generate_presigned_url.return_value = "http://localhost:9000/b/k?X-Amz-Signature=abc"
    mock_client_factory.side_effect = [put_client, sign_client]

    url = public_url(_s3_settings(), "k")
    assert "X-Amz-Signature" in url


@patch("app.storage.boto3.client")
def test_get_storage_signed_url_helper(mock_client_factory):
    put_client = MagicMock()
    sign_client = MagicMock()
    sign_client.generate_presigned_url.return_value = "http://localhost:9000/b/k?sig=1"
    mock_client_factory.side_effect = [put_client, sign_client]

    url = signed_url(_s3_settings(), "k")
    assert url.endswith("sig=1") or "sig=1" in url


def test_gcs_signed_url_passes_credentials_and_ttl():
    settings = Settings(
        storage_provider="gcs",
        gcs_bucket="moderation-content",
        gcp_project="demo",
        signed_url_ttl_seconds=600,
        google_application_credentials="",
    )
    store = GcsObjectStorage.__new__(GcsObjectStorage)
    store.settings = settings
    store._signing_credentials = object()
    blob = MagicMock()
    blob.generate_signed_url.return_value = "https://storage.googleapis.com/moderation-content/k?X-Goog-Signature=1"
    bucket = MagicMock()
    bucket.blob.return_value = blob
    store._bucket = bucket

    url = store.signed_url("k")

    assert "X-Goog-Signature" in url
    kwargs = blob.generate_signed_url.call_args.kwargs
    assert kwargs["version"] == "v4"
    assert kwargs["method"] == "GET"
    assert kwargs["expiration"] == timedelta(seconds=600)
    assert kwargs["credentials"] is store._signing_credentials
