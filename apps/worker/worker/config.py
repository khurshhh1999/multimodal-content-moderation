from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://moderation:moderation@localhost:5432/moderation"
    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint_url: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "moderation-content"
    s3_region: str = "us-east-1"

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
    max_upload_bytes: int = 10 * 1024 * 1024
    poll_wait_seconds: int = 10
    visibility_timeout: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
