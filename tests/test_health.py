from ecochronos_vault.health import DependencyStatus, check_minio, check_postgres


def test_postgres_skipped_when_dsn_missing(settings) -> None:
    assert check_postgres(settings) == DependencyStatus.SKIPPED


def test_minio_skipped_when_endpoint_missing(settings) -> None:
    assert check_minio(settings) == DependencyStatus.SKIPPED


def test_postgres_error_when_dsn_unreachable(settings) -> None:
    settings.postgres_dsn = "postgresql://ecochronos:bad@127.0.0.1:1/ecochronos"
    assert check_postgres(settings) == DependencyStatus.ERROR


def test_minio_error_when_endpoint_unreachable(settings) -> None:
    settings.minio_endpoint = "127.0.0.1:1"
    settings.minio_access_key = "ecochronos"
    settings.minio_secret_key = "ecochronos_local_dev_minio"
    assert check_minio(settings) == DependencyStatus.ERROR
