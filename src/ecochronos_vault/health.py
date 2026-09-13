from __future__ import annotations

from enum import StrEnum

import urllib3
from psycopg import connect

from ecochronos_vault.archive import build_minio_client, minio_configured
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
    if not minio_configured(settings):
        return DependencyStatus.SKIPPED
    try:
        client = build_minio_client(
            settings,
            timeout=_HTTP_TIMEOUT,
            retries=False,
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
