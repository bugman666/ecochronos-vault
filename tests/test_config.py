import pytest

from ecochronos_vault.config import Settings


def test_default_bind_address_and_port() -> None:
    settings = Settings(_env_file=None)
    assert settings.api_host == "0.0.0.0"
    assert settings.api_port == 8000
    assert settings.log_level == "info"
    assert settings.postgres_dsn is None
    assert settings.minio_bucket == "ecochronos"
    assert settings.archive_upload_token is None


def test_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_HOST", "127.0.0.1")
    monkeypatch.setenv("API_PORT", "9001")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv(
        "POSTGRES_DSN",
        "postgresql://ecochronos:ecochronos_local_dev@127.0.0.1:5432/ecochronos",
    )
    monkeypatch.setenv("MINIO_ENDPOINT", "127.0.0.1:9000")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "ecochronos")
    monkeypatch.setenv("MINIO_SECRET_KEY", "ecochronos_local_dev_minio")
    monkeypatch.setenv("MINIO_BUCKET", "vault-archive")
    monkeypatch.setenv("MINIO_SECURE", "false")
    monkeypatch.setenv("ARCHIVE_UPLOAD_TOKEN", "  ")

    settings = Settings(_env_file=None)
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 9001
    assert settings.log_level == "debug"
    assert settings.postgres_dsn.endswith("/ecochronos")
    assert settings.minio_endpoint == "127.0.0.1:9000"
    assert settings.minio_access_key == "ecochronos"
    assert settings.minio_secret_key == "ecochronos_local_dev_minio"
    assert settings.minio_bucket == "vault-archive"
    assert settings.minio_secure is False
    assert settings.archive_upload_token is None


def test_archive_upload_token_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARCHIVE_UPLOAD_TOKEN", "ingest-token")
    settings = Settings(_env_file=None)
    assert settings.archive_upload_token == "ingest-token"
