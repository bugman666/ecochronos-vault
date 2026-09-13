from functools import lru_cache

from pydantic import field_validator
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

    @field_validator("archive_upload_token", mode="before")
    @classmethod
    def _blank_upload_token(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
