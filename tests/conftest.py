from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ecochronos_vault.app import create_app
from ecochronos_vault.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
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
        data_dir=tmp_path / "data",
        ingest_source="openaq",
        ingest_schedule_enabled=False,
        ingest_interval_seconds=86400,
        ingest_skip_existing=False,
        ingest_http_timeout_seconds=5.0,
        openaq_base_url="https://api.openaq.org/v3",
        openaq_api_key="test-openaq-key",
        openaq_limit=100,
        openaq_iso=None,
        _env_file=None,
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
