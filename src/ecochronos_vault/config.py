from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process settings loaded from the environment (and optional .env)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "info"

    postgres_dsn: str | None = None

    minio_endpoint: str | None = None
    minio_access_key: str | None = None
    minio_secret_key: str | None = None
    minio_bucket: str = "ecochronos"
    minio_secure: bool = False

    # When empty, HTTP PUT /archive/... is disabled (open archive is read-only).
    archive_upload_token: str | None = None

    data_dir: Path = Path("data")

    ingest_source: str = "openaq"
    ingest_schedule_enabled: bool = False
    ingest_interval_seconds: int = Field(default=86400, ge=1)
    ingest_skip_existing: bool = False
    ingest_http_timeout_seconds: float = Field(default=30.0, gt=0)

    openaq_base_url: str = "https://api.openaq.org/v3"
    openaq_api_key: str | None = None
    openaq_limit: int = Field(default=100, ge=1, le=1000)
    openaq_iso: str | None = None

    staging_dir: Path = Path("data/staging")
    processed_dir: Path = Path("data/processed")
    resample_rule: str = "daily_mean_by_station"
    output_format: str = "parquet"

    @field_validator("archive_upload_token", mode="before")
    @classmethod
    def _blank_upload_token(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
