from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
