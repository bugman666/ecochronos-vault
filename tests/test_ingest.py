from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from ecochronos_vault.app import create_app
from ecochronos_vault.config import Settings
from ecochronos_vault.ingest import build_ingest_status_view, run_daily_ingest
from ecochronos_vault.ingest.client import locations_url
from ecochronos_vault.ingest.scheduler import JOB_ID, start_scheduler
from ecochronos_vault.ingest.store import artifact_path, status_path


def _openaq_body(location_id: int = 8118) -> dict:
    return {
        "meta": {"name": "openaq-api", "page": 1, "limit": 100, "found": 1},
        "results": [
            {
                "id": location_id,
                "name": "New Delhi",
                "timezone": "Asia/Kolkata",
                "country": {"id": 9, "code": "IN", "name": "India"},
                "coordinates": {"latitude": 28.63576, "longitude": 77.22445},
            }
        ],
    }


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ingest_writes_lightly_staged_json(settings: Settings) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_openaq_body())

    result = run_daily_ingest(
        settings,
        run_date=date(2026, 9, 13),
        http_client=_mock_client(handler),
    )

    assert result.status == "success"
    assert result.overwritten is False
    assert result.skipped is False
    assert result.path is not None
    path = Path(result.path)
    assert path == artifact_path(settings.data_dir, "openaq", date(2026, 9, 13))
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["source"] == "openaq"
    assert saved["endpoint"] == "locations"
    assert saved["run_date"] == "2026-09-13"
    assert saved["payload"]["results"][0]["id"] == 8118
    assert calls[0].url.path.endswith("/locations")
    assert calls[0].headers["X-API-Key"] == "test-openaq-key"
    assert status_path(settings.data_dir).exists()
    leftover = path.with_name(f".{path.name}.tmp")
    assert not leftover.exists()


def test_reingest_same_day_overwrites_atomically(settings: Settings) -> None:
    def first(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openaq_body(1))

    def second(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openaq_body(2))

    first_result = run_daily_ingest(
        settings,
        run_date=date(2026, 9, 13),
        http_client=_mock_client(first),
    )
    second_result = run_daily_ingest(
        settings,
        run_date=date(2026, 9, 13),
        http_client=_mock_client(second),
    )

    assert first_result.status == "success"
    assert second_result.status == "success"
    assert second_result.overwritten is True
    saved = json.loads(Path(second_result.path).read_text(encoding="utf-8"))
    assert saved["payload"]["results"][0]["id"] == 2
    leftover = Path(second_result.path).with_name(".locations.json.tmp")
    assert not leftover.exists()


def test_skip_existing_does_not_call_http(settings: Settings) -> None:
    settings.ingest_skip_existing = True
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_openaq_body())

    run_daily_ingest(
        settings,
        run_date=date(2026, 9, 13),
        http_client=_mock_client(handler),
    )
    result = run_daily_ingest(
        settings,
        run_date=date(2026, 9, 13),
        skip_existing=True,
        http_client=_mock_client(handler),
    )

    assert result.status == "skipped"
    assert result.skipped is True
    assert calls == 1


def test_missing_api_key_is_error_and_does_not_write_artifact(settings: Settings) -> None:
    settings.openaq_api_key = None

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("HTTP should not run without an API key")

    result = run_daily_ingest(
        settings,
        run_date=date(2026, 9, 13),
        http_client=_mock_client(handler),
    )
    assert result.status == "error"
    assert result.error is not None
    assert "OPENAQ_API_KEY" in result.error
    assert not artifact_path(settings.data_dir, "openaq", date(2026, 9, 13)).exists()
    status = build_ingest_status_view(settings)
    assert status["last_run"]["status"] == "error"


def test_non_json_response_is_persisted_raw(settings: Settings) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json", headers={"content-type": "text/plain"})

    result = run_daily_ingest(
        settings,
        run_date=date(2026, 9, 13),
        http_client=_mock_client(handler),
    )
    assert result.status == "success"
    assert Path(result.path).read_bytes() == b"not-json"


def test_http_error_records_status(settings: Settings) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": "unavailable"})

    result = run_daily_ingest(
        settings,
        run_date=date(2026, 9, 13),
        http_client=_mock_client(handler),
    )
    assert result.status == "error"
    assert "503" in (result.error or "")
    assert not artifact_path(settings.data_dir, "openaq", date(2026, 9, 13)).exists()


def test_unsupported_source(settings: Settings) -> None:
    settings.ingest_source = "noaa"

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not fetch")

    result = run_daily_ingest(settings, http_client=_mock_client(handler))
    assert result.status == "error"
    assert "unsupported ingest source" in (result.error or "")


def test_ingest_status_endpoint_empty(client: TestClient) -> None:
    response = client.get("/ingest/status")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "openaq"
    assert body["schedule_enabled"] is False
    assert body["last_run"] is None


def test_ingest_status_endpoint_after_run(settings: Settings) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_openaq_body())

    run_daily_ingest(
        settings,
        run_date=date(2026, 9, 13),
        http_client=_mock_client(handler),
    )
    response = TestClient(create_app(settings)).get("/ingest/status")
    assert response.status_code == 200
    body = response.json()
    assert body["last_run"]["status"] == "success"
    assert body["last_run"]["run_date"] == "2026-09-13"
    assert body["last_run"]["path"].endswith("raw/openaq/2026-09-13/locations.json")


def test_iso_filter_is_passed(settings: Settings) -> None:
    settings.openaq_iso = "us"
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json=_openaq_body())

    run_daily_ingest(settings, http_client=_mock_client(handler))
    assert seen["iso"] == "US"
    assert seen["limit"] == "100"


def test_locations_url_strips_slash(settings: Settings) -> None:
    settings.openaq_base_url = "https://api.openaq.org/v3/"
    assert locations_url(settings) == "https://api.openaq.org/v3/locations"


def test_scheduler_registers_interval_job(settings: Settings) -> None:
    settings.ingest_interval_seconds = 3600
    ran: list[int] = []
    scheduler = start_scheduler(
        settings,
        job_func=lambda: ran.append(1),
        run_immediately=False,
    )
    try:
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1
        assert jobs[0].id == JOB_ID
        assert scheduler.running
    finally:
        scheduler.shutdown(wait=False)


def test_create_app_does_not_start_scheduler_by_default(settings: Settings) -> None:
    with TestClient(create_app(settings)) as test_client:
        assert test_client.app.state.ingest_scheduler is None
        assert test_client.get("/ingest/status").status_code == 200


def test_lifespan_starts_scheduler_when_enabled(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.ingest_schedule_enabled = True
    fake = SimpleNamespace(shutdown_calls=0)

    def shutdown(*, wait: bool = False) -> None:
        fake.shutdown_calls += 1

    fake.shutdown = shutdown

    monkeypatch.setattr(
        "ecochronos_vault.app.start_scheduler",
        lambda _settings, run_immediately=True: fake,
    )
    with TestClient(create_app(settings)) as test_client:
        assert test_client.app.state.ingest_scheduler is fake
    assert fake.shutdown_calls == 1
