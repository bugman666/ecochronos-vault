import hashlib
from pathlib import Path

import duckdb
import pytest

from ecochronos_vault.batch import (
    OUTPUT_FILENAME,
    BatchError,
    main,
    run_batch,
    write_sha256,
)
from ecochronos_vault.config import Settings

FIXTURE_STAGING = Path(__file__).parent / "fixtures" / "staging"


def _settings(tmp_path: Path, staging: Path | None = None) -> Settings:
    return Settings(
        api_host="0.0.0.0",
        api_port=8000,
        log_level="info",
        postgres_dsn=None,
        minio_endpoint=None,
        staging_dir=staging or FIXTURE_STAGING,
        processed_dir=tmp_path / "processed",
        resample_rule="daily_mean_by_station",
        output_format="parquet",
        _env_file=None,
    )


def _read_parquet(path: Path) -> list[tuple]:
    connection = duckdb.connect()
    try:
        return connection.execute(
            """
            SELECT date, station_id, lon, lat, value, n_obs
            FROM read_parquet(?)
            ORDER BY date, station_id
            """,
            [str(path)],
        ).fetchall()
    finally:
        connection.close()


def test_run_batch_cleans_resamples_and_writes_parquet(tmp_path: Path) -> None:
    result = run_batch(_settings(tmp_path))

    assert result.output_path.name == OUTPUT_FILENAME
    assert result.output_path.is_file()
    assert result.checksum_path.is_file()
    assert result.rows_read == 14
    assert result.rows_kept == 4
    assert result.rows_written == 3
    assert {path.name for path in result.input_files} == {
        "aliases.csv",
        "observations.csv",
    }

    rows = _read_parquet(result.output_path)
    assert len(rows) == 3

    jan1_alpha, jan1_beta, jan2_alpha = rows
    assert jan1_alpha[0].isoformat() == "2024-01-01"
    assert jan1_alpha[1] == "alpha"
    assert jan1_alpha[2] == pytest.approx(120.50)
    assert jan1_alpha[3] == pytest.approx(30.20)
    assert jan1_alpha[4] == pytest.approx(12.0)
    assert jan1_alpha[5] == 3

    assert jan1_beta[1] == "beta"
    assert jan1_beta[4] == pytest.approx(20.0)
    assert jan1_beta[5] == 1

    assert jan2_alpha[0].isoformat() == "2024-01-02"
    assert jan2_alpha[1] == "alpha"
    assert jan2_alpha[4] == pytest.approx(12.0)
    assert jan2_alpha[5] == 1


def test_empty_and_unusable_rows_are_dropped(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "only-bad.csv").write_text(
        "time,station_id,lon,lat,value\n"
        ",,,,\n"
        "not-a-time,alpha,120.5,30.2,1\n"
        "2024-01-01T00:00:00Z,alpha,999,30.2,1\n"
        "2024-01-01T00:00:00Z,alpha,120.5,91,1\n"
        "2024-01-01T00:00:00Z,,120.5,30.2,1\n"
        "2024-01-01T00:00:00Z,alpha,120.5,30.2,\n",
        encoding="utf-8",
    )

    result = run_batch(_settings(tmp_path, staging=staging))
    assert result.rows_read == 6
    assert result.rows_kept == 0
    assert result.rows_written == 0
    assert _read_parquet(result.output_path) == []


def test_checksum_sidecar_matches_bytes(tmp_path: Path) -> None:
    result = run_batch(_settings(tmp_path))
    expected = hashlib.sha256(result.output_path.read_bytes()).hexdigest()
    assert result.sha256 == expected
    assert result.checksum_path.read_text(encoding="utf-8") == (
        f"{expected}  {result.output_path.name}\n"
    )
    digest, sidecar = write_sha256(result.output_path)
    assert digest == expected
    assert sidecar == result.checksum_path


def test_missing_staging_raises(tmp_path: Path) -> None:
    with pytest.raises(BatchError, match="staging directory not found"):
        run_batch(_settings(tmp_path, staging=tmp_path / "missing"))


def test_no_csv_raises(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(BatchError, match="no CSV files"):
        run_batch(_settings(tmp_path, staging=staging))


def test_unsupported_rule_and_format(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(BatchError, match="unsupported resample_rule"):
        run_batch(settings, resample_rule="hourly_grid")
    with pytest.raises(BatchError, match="unsupported output_format"):
        run_batch(settings, output_format="netcdf")


def test_cli_writes_parquet_from_fixture(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    processed = tmp_path / "out"
    code = main(
        [
            "--staging-dir",
            str(FIXTURE_STAGING),
            "--processed-dir",
            str(processed),
        ]
    )
    assert code == 0
    payload = capsys.readouterr().out
    output = processed / OUTPUT_FILENAME
    checksum = processed / f"{OUTPUT_FILENAME}.sha256"
    assert output.is_file()
    assert checksum.is_file()
    assert str(output) in payload
    assert "sha256" in payload


def test_cli_errors_on_empty_staging(tmp_path: Path) -> None:
    staging = tmp_path / "empty"
    staging.mkdir()
    code = main(["--staging-dir", str(staging), "--processed-dir", str(tmp_path / "out")])
    assert code == 1
