from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://moderation:moderation@localhost:5432/moderation"
    redis_url: str = "redis://localhost:6379/0"

    storage_provider: str = "s3"  # s3 | gcs
    queue_provider: str = "sqs"  # sqs | pubsub

    s3_endpoint_url: str = "http://localhost:9000"
    s3_public_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "moderation-content"
    s3_region: str = "us-east-1"

    gcs_bucket: str = "moderation-content"
    gcp_project: str = ""
    google_application_credentials: str = ""
    pubsub_topic: str = "moderation-jobs"

    # TTL for review/decision image links (S3/MinIO presign or GCS V4 signed URL).
    signed_url_ttl_seconds: int = 900

    sqs_endpoint_url: str = "http://localhost:4566"
    sqs_queue_url: str = "http://localhost:4566/000000000000/moderation-jobs"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    aws_default_region: str = "us-east-1"

    policy_version: str = "policy-v1"
    pipeline_version: str = "pipeline-v1"
    max_upload_bytes: int = 10 * 1024 * 1024
    cors_origins: str = "http://localhost:5173"
    allowed_content_types: str = "image/jpeg,image/png,image/webp,image/gif"

    # Per-tenant ingest rate limit (Redis fixed window). Set RATE_LIMIT_REQUESTS=0 to disable.
    default_tenant_id: str = "default"
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

    # OpenTelemetry (OTLP/HTTP → Jaeger or any collector).
    otel_enabled: bool = False
    otel_service_name: str = "moderation-api"
    otel_exporter_otlp_endpoint: str = ""
    otel_console_exporter: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
