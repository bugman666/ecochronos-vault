from __future__ import annotations

from enum import StrEnum

import urllib3
from minio import Minio
from psycopg import connect

from ecochronos_vault.config import Settings

_HTTP_TIMEOUT = urllib3.Timeout(connect=2.0, read=2.0)


class DependencyStatus(StrEnum):
    CONNECTED = "connected"
    SKIPPED = "skipped"
    ERROR = "error"


def _configured(value: str | None) -> bool:
    return bool(value and value.strip())


def check_postgres(settings: Settings) -> DependencyStatus:
    if not _configured(settings.postgres_dsn):
        return DependencyStatus.SKIPPED
    try:
        with connect(settings.postgres_dsn, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return DependencyStatus.CONNECTED
    except Exception:
        return DependencyStatus.ERROR


def check_minio(settings: Settings) -> DependencyStatus:
    if not (
        _configured(settings.minio_endpoint)
        and _configured(settings.minio_access_key)
        and _configured(settings.minio_secret_key)
    ):
        return DependencyStatus.SKIPPED
    try:
        http_client = urllib3.PoolManager(timeout=_HTTP_TIMEOUT, retries=False)
        client = Minio(
            settings.minio_endpoint.strip(),
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            http_client=http_client,
        )
        client.list_buckets()
        return DependencyStatus.CONNECTED
    except Exception:
        return DependencyStatus.ERROR


def collect_dependency_status(settings: Settings) -> dict[str, str]:
    return {
        "postgres": check_postgres(settings).value,
        "minio": check_minio(settings).value,
    }
