from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_name: str = "AI Short Drama Studio"
    environment: str = "development"
    use_celery: bool = False
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672//"
    celery_result_backend: str = "rpc://"
    s3_bucket: str = "short-drama-assets"
    s3_region: str = "ap-southeast-1"
    max_revision_attempts: int = 2
    media_concurrency: int = 4
    model_config_encryption_key: str | None = None


def get_settings() -> Settings:
    return Settings(
        environment=os.getenv("APP_ENV", "development"),
        use_celery=os.getenv("USE_CELERY", "false").lower() == "true",
        rabbitmq_url=os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672//"),
        celery_result_backend=os.getenv("CELERY_RESULT_BACKEND", "rpc://"),
        s3_bucket=os.getenv("S3_BUCKET", "short-drama-assets"),
        s3_region=os.getenv("AWS_REGION", "ap-southeast-1"),
        max_revision_attempts=int(os.getenv("MAX_REVISION_ATTEMPTS", "2")),
        media_concurrency=int(os.getenv("MEDIA_CONCURRENCY", "4")),
        model_config_encryption_key=os.getenv("MODEL_CONFIG_ENCRYPTION_KEY"),
    )

