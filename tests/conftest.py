import pytest
from fastapi.testclient import TestClient

from ecochronos_vault.app import create_app
from ecochronos_vault.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        api_host="0.0.0.0",
        api_port=8000,
        log_level="info",
        postgres_dsn=None,
        minio_endpoint=None,
        minio_access_key=None,
        minio_secret_key=None,
        minio_bucket="ecochronos",
        minio_secure=False,
        _env_file=None,
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))
