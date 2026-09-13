from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from ecochronos_vault.config import Settings
from ecochronos_vault.ingest.client import (
    IngestConfigError,
    IngestError,
    fetch_openaq_locations,
    location_query_params,
)
from ecochronos_vault.ingest.store import (
    artifact_path,
    load_status,
    write_bytes_atomic,
    write_status,
)

logger = logging.getLogger(__name__)

# TODO(#3): after a successful raw write, clean/resample and emit Parquet (or NetCDF/Zarr).
# TODO(#4): optionally push the raw artifact through ArchiveStore after a successful fetch.
# TODO(#5): register path, bbox, time range, and checksum in PostGIS.

StatusName = Literal["success", "skipped", "error"]


@dataclass(frozen=True)
class IngestResult:
    source: str
    run_at: datetime
    run_date: date
    status: StatusName
    path: str | None
    bytes_written: int
    overwritten: bool
    skipped: bool
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "run_at": self.run_at.isoformat(),
            "run_date": self.run_date.isoformat(),
            "status": self.status,
            "path": self.path,
            "bytes_written": self.bytes_written,
            "overwritten": self.overwritten,
            "skipped": self.skipped,
            "error": self.error,
        }


def load_ingest_status(data_dir: Path) -> dict[str, Any] | None:
    return load_status(data_dir)


def build_ingest_status_view(settings: Settings) -> dict[str, Any]:
    return {
        "source": settings.ingest_source,
        "schedule_enabled": settings.ingest_schedule_enabled,
        "interval_seconds": settings.ingest_interval_seconds,
        "skip_existing": settings.ingest_skip_existing,
        "data_dir": str(settings.data_dir),
        "openaq_base_url": settings.openaq_base_url,
        "openaq_limit": settings.openaq_limit,
        "openaq_iso": settings.openaq_iso,
        "last_run": load_status(settings.data_dir),
    }


def _persist_result(settings: Settings, result: IngestResult) -> None:
    write_status(settings.data_dir, result.to_dict())


def _error_result(
    settings: Settings,
    *,
    run_at: datetime,
    run_date: date,
    error: str,
) -> IngestResult:
    result = IngestResult(
        source=settings.ingest_source,
        run_at=run_at,
        run_date=run_date,
        status="error",
        path=None,
        bytes_written=0,
        overwritten=False,
        skipped=False,
        error=error,
    )
    _persist_result(settings, result)
    return result


def _envelope(*, fetched_at: datetime, run_date: date, request_url: str, request: dict[str, Any], payload: Any) -> bytes:
    body = {
        "source": "openaq",
        "endpoint": "locations",
        "fetched_at": fetched_at.isoformat(),
        "run_date": run_date.isoformat(),
        "request": {
            "url": request_url,
            **request,
        },
        "payload": payload,
    }
    return json.dumps(body, indent=2, ensure_ascii=False).encode("utf-8")


def run_daily_ingest(
    settings: Settings,
    *,
    run_date: date | None = None,
    skip_existing: bool | None = None,
    http_client: httpx.Client | None = None,
) -> IngestResult:
    """Fetch one OpenAQ locations page and persist it under data/raw.

    Re-running the same UTC day overwrites the artifact atomically unless
    `skip_existing` is set. Status is always written so `/ingest/status` stays current.
    """
    run_at = datetime.now(UTC)
    day = run_date or run_at.date()
    skip = settings.ingest_skip_existing if skip_existing is None else skip_existing
    target = artifact_path(settings.data_dir, settings.ingest_source, day)

    if skip and target.exists():
        result = IngestResult(
            source=settings.ingest_source,
            run_at=run_at,
            run_date=day,
            status="skipped",
            path=str(target),
            bytes_written=target.stat().st_size,
            overwritten=False,
            skipped=True,
            error=None,
        )
        _persist_result(settings, result)
        logger.info("ingest skipped; artifact already exists path=%s", target)
        return result

    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=settings.ingest_http_timeout_seconds)
    try:
        response = fetch_openaq_locations(settings, client)
        try:
            payload: Any = response.json()
            content = _envelope(
                fetched_at=run_at,
                run_date=day,
                request_url=str(response.url),
                request=location_query_params(settings),
                payload=payload,
            )
        except json.JSONDecodeError:
            content = response.content
        overwritten = write_bytes_atomic(target, content)
        leftover_tmp = target.with_name(f".{target.name}.tmp")
        if leftover_tmp.exists():
            leftover_tmp.unlink()
        result = IngestResult(
            source=settings.ingest_source,
            run_at=run_at,
            run_date=day,
            status="success",
            path=str(target),
            bytes_written=len(content),
            overwritten=overwritten,
            skipped=False,
            error=None,
        )
        _persist_result(settings, result)
        logger.info(
            "ingest wrote path=%s bytes=%s overwritten=%s",
            target,
            result.bytes_written,
            overwritten,
        )
        return result
    except (IngestConfigError, IngestError, OSError, httpx.HTTPError) as exc:
        logger.warning("ingest failed: %s", exc)
        return _error_result(settings, run_at=run_at, run_date=day, error=str(exc))
    finally:
        if owns_client:
            client.close()
