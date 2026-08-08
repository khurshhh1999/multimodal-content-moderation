from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://moderation:moderation@localhost:5432/moderation"
    redis_url: str = "redis://localhost:6379/0"

    storage_provider: str = "s3"  # s3 | gcs
    queue_provider: str = "sqs"  # sqs | pubsub

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "moderation-content"
    s3_region: str = "us-east-1"

    gcs_bucket: str = "moderation-content"
    gcp_project: str = ""
    google_application_credentials: str = ""
    pubsub_subscription: str = "moderation-jobs-sub"

    sqs_endpoint_url: str = "http://localhost:4566"
    sqs_queue_url: str = "http://localhost:4566/000000000000/moderation-jobs"
    sqs_dlq_url: str = "http://localhost:4566/000000000000/moderation-jobs-dlq"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    aws_default_region: str = "us-east-1"

    vision_provider: str = "local"  # local | aws | gcp
    llm_provider: str = "rules"  # rules | openai
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    policy_version: str = "policy-v1"
    pipeline_version: str = "pipeline-v1"
    # Threshold bands — bump POLICY_VERSION when these change
    auto_allow: float = 0.85
    auto_block: float = 0.90
    nsfw_block: float = 0.85
    nsfw_flag: float = 0.45
    violence_block: float = 0.80
    violence_flag: float = 0.40
    max_upload_bytes: int = 10 * 1024 * 1024
    poll_wait_seconds: int = 10
    visibility_timeout: int = 60

    # OpenTelemetry (OTLP/HTTP → Jaeger or any collector).
    otel_enabled: bool = False
    otel_service_name: str = "moderation-worker"
    otel_exporter_otlp_endpoint: str = ""
    otel_console_exporter: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
