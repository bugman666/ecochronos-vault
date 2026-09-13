from __future__ import annotations

import json
from datetime import UTC, date, datetime

import httpx
import pytest

from ecochronos_vault.config import get_settings
from ecochronos_vault.ingest.runner import IngestResult
from ecochronos_vault.main import build_parser, main


def test_parser_defaults_to_no_subcommand() -> None:
    args = build_parser().parse_args([])
    assert args.command is None


def test_parser_ingest_date_and_skip() -> None:
    args = build_parser().parse_args(
        ["ingest", "--date", "2026-09-13", "--skip-existing"]
    )
    assert args.command == "ingest"
    assert args.run_date == date(2026, 9, 13)
    assert args.skip_existing is True


def test_cli_ingest_status_never_run(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INGEST_SCHEDULE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        assert main(["ingest-status"]) == 0
    finally:
        get_settings.cache_clear()
    body = json.loads(capsys.readouterr().out)
    assert body["source"] == "openaq"
    assert body["last_run"] is None


def test_cli_ingest_uses_runner(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    result = IngestResult(
        source="openaq",
        run_at=datetime(2026, 9, 13, tzinfo=UTC),
        run_date=date(2026, 9, 13),
        status="success",
        path="data/raw/openaq/2026-09-13/locations.json",
        bytes_written=12,
        overwritten=False,
        skipped=False,
        error=None,
    )
    monkeypatch.setattr(
        "ecochronos_vault.main.run_daily_ingest",
        lambda settings, run_date=None, skip_existing=None, http_client=None: result,
    )
    get_settings.cache_clear()
    try:
        assert main(["ingest", "--date", "2026-09-13"]) == 0
    finally:
        get_settings.cache_clear()
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "success"
    assert printed["run_date"] == "2026-09-13"


def test_cli_ingest_error_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    result = IngestResult(
        source="openaq",
        run_at=datetime(2026, 9, 13, tzinfo=UTC),
        run_date=date(2026, 9, 13),
        status="error",
        path=None,
        bytes_written=0,
        overwritten=False,
        skipped=False,
        error="boom",
    )
    monkeypatch.setattr(
        "ecochronos_vault.main.run_daily_ingest",
        lambda settings, run_date=None, skip_existing=None, http_client=None: result,
    )
    get_settings.cache_clear()
    try:
        assert main(["ingest"]) == 1
    finally:
        get_settings.cache_clear()


def test_httpx_is_available_for_mocks() -> None:
    assert httpx.MockTransport is not None
