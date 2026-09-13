"""Local batch pipeline: clean staged CSVs, resample, write Parquet + checksum.

Contract
--------
Input: ``*.csv`` files in ``staging_dir`` (default ``data/staging``). Headers are
case-insensitive. Required fields and accepted aliases:

* time: ``time``, ``timestamp``, ``datetime``, ``date`` (UTC; date-only is midnight UTC)
* station_id: ``station_id``, ``station``, ``site_id``, ``site``
* lon: ``lon``, ``longitude``, ``x`` (must fall in ``[-180, 180]``)
* lat: ``lat``, ``latitude``, ``y`` (must fall in ``[-90, 90]``)
* value: ``value``, ``val``, ``observation``, ``measure``

Cleaning: drop empty rows and rows whose time, station, coordinates, or value
cannot be coerced. Out-of-range lon/lat are dropped.

Resample rule ``daily_mean_by_station``: group by UTC calendar day + station_id.
``value`` and lon/lat are averaged; ``n_obs`` is the number of usable rows in
that group.

Output (default ``data/processed``):

* ``daily_mean_by_station.parquet``
* ``daily_mean_by_station.parquet.sha256`` (``<hex>  <filename>``)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import duckdb

from ecochronos_vault.config import Settings, get_settings

LOGGER = logging.getLogger(__name__)

RESAMPLE_DAILY_MEAN_BY_STATION = "daily_mean_by_station"
OUTPUT_PARQUET = "parquet"
OUTPUT_FILENAME = "daily_mean_by_station.parquet"

REQUIRED_FIELDS = ("time", "station_id", "lon", "lat", "value")

COLUMN_ALIASES: dict[str, str] = {
    "time": "time",
    "timestamp": "time",
    "datetime": "time",
    "date": "time",
    "station_id": "station_id",
    "station": "station_id",
    "site_id": "station_id",
    "site": "station_id",
    "lon": "lon",
    "longitude": "lon",
    "x": "lon",
    "lat": "lat",
    "latitude": "lat",
    "y": "lat",
    "value": "value",
    "val": "value",
    "observation": "value",
    "measure": "value",
}


class BatchError(Exception):
    """Staging inputs or settings are not usable."""


@dataclass(frozen=True)
class BatchResult:
    output_path: Path
    checksum_path: Path
    sha256: str
    input_files: tuple[Path, ...]
    rows_read: int
    rows_kept: int
    rows_written: int
    resample_rule: str
    output_format: str

    def to_dict(self) -> dict[str, object]:
        return {
            "output_path": str(self.output_path),
            "checksum_path": str(self.checksum_path),
            "sha256": self.sha256,
            "input_files": [str(path) for path in self.input_files],
            "rows_read": self.rows_read,
            "rows_kept": self.rows_kept,
            "rows_written": self.rows_written,
            "resample_rule": self.resample_rule,
            "output_format": self.output_format,
        }


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _resolve(path: Path) -> Path:
    return path.expanduser().resolve()


def _read_header(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        try:
            return next(csv.reader(handle))
        except StopIteration:
            return []


def map_headers(path: Path) -> dict[str, str]:
    """Map canonical field name → original CSV header (first alias wins)."""
    mapped: dict[str, str] = {}
    for raw in _read_header(path):
        canonical = COLUMN_ALIASES.get(raw.strip().lower())
        if canonical and canonical not in mapped:
            mapped[canonical] = raw
    return mapped


def list_staging_csvs(staging_dir: Path) -> list[Path]:
    if staging_dir.is_file():
        if staging_dir.suffix.lower() != ".csv":
            raise BatchError(f"staging path is not a CSV file: {staging_dir}")
        return [staging_dir]
    if not staging_dir.exists():
        raise BatchError(f"staging directory not found: {staging_dir}")
    if not staging_dir.is_dir():
        raise BatchError(f"staging path is not a directory: {staging_dir}")
    return sorted(path for path in staging_dir.glob("*.csv") if path.is_file())


def write_sha256(path: Path) -> tuple[str, Path]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    sidecar = path.with_name(path.name + ".sha256")
    sidecar.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return digest, sidecar


def _validate_rule_and_format(resample_rule: str, output_format: str) -> None:
    if resample_rule != RESAMPLE_DAILY_MEAN_BY_STATION:
        raise BatchError(
            f"unsupported resample_rule {resample_rule!r}; "
            f"only {RESAMPLE_DAILY_MEAN_BY_STATION!r} is implemented"
        )
    if output_format != OUTPUT_PARQUET:
        raise BatchError(
            f"unsupported output_format {output_format!r}; "
            f"only {OUTPUT_PARQUET!r} is implemented"
        )


def run_batch(
    settings: Settings | None = None,
    *,
    staging_dir: Path | str | None = None,
    processed_dir: Path | str | None = None,
    resample_rule: str | None = None,
    output_format: str | None = None,
) -> BatchResult:
    """Clean and resample staged CSVs into a Parquet artifact.

    Later ingest can call this with a Settings object or explicit directories.
    """
    settings = settings or get_settings()
    staging = _resolve(Path(staging_dir or settings.staging_dir))
    processed = _resolve(Path(processed_dir or settings.processed_dir))
    rule = resample_rule or settings.resample_rule
    fmt = output_format or settings.output_format
    _validate_rule_and_format(rule, fmt)

    input_files = list_staging_csvs(staging)
    if not input_files:
        raise BatchError(f"no CSV files in {staging}")

    processed.mkdir(parents=True, exist_ok=True)
    output_path = processed / OUTPUT_FILENAME
    tmp_path = processed / f".{OUTPUT_FILENAME}.tmp"

    connection = duckdb.connect()
    try:
        parts: list[str] = []
        for index, path in enumerate(input_files):
            mapped = map_headers(path)
            missing = [field for field in REQUIRED_FIELDS if field not in mapped]
            if missing:
                raise BatchError(
                    f"{path.name} is missing required columns {missing} "
                    f"(need time, station_id, lon, lat, value; aliases allowed)"
                )
            raw_name = f"_raw_{index}"
            view_name = f"src_{index}"
            relation = connection.read_csv(str(path), header=True, all_varchar=True)
            connection.register(raw_name, relation)
            connection.execute(
                f"""
                CREATE OR REPLACE VIEW {view_name} AS
                SELECT
                  {_ident(mapped['time'])} AS time,
                  {_ident(mapped['station_id'])} AS station_id,
                  {_ident(mapped['lon'])} AS lon,
                  {_ident(mapped['lat'])} AS lat,
                  {_ident(mapped['value'])} AS value
                FROM {raw_name}
                """
            )
            parts.append(f"SELECT * FROM {view_name}")

        connection.execute("CREATE OR REPLACE VIEW staged AS " + " UNION ALL ".join(parts))
        connection.execute(
            """
            CREATE OR REPLACE VIEW cleaned AS
            SELECT
              COALESCE(
                try_cast(trimmed_time AS TIMESTAMPTZ),
                try_cast(trimmed_time AS TIMESTAMP),
                CAST(try_cast(trimmed_time AS DATE) AS TIMESTAMP)
              ) AS ts,
              nullif(trim(CAST(station_id AS VARCHAR)), '') AS station_id,
              try_cast(nullif(trim(CAST(lon AS VARCHAR)), '') AS DOUBLE) AS lon,
              try_cast(nullif(trim(CAST(lat AS VARCHAR)), '') AS DOUBLE) AS lat,
              try_cast(nullif(trim(CAST(value AS VARCHAR)), '') AS DOUBLE) AS value
            FROM (
              SELECT
                nullif(trim(CAST(time AS VARCHAR)), '') AS trimmed_time,
                station_id,
                lon,
                lat,
                value
              FROM staged
            )
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE VIEW usable AS
            SELECT ts, station_id, lon, lat, value
            FROM cleaned
            WHERE ts IS NOT NULL
              AND station_id IS NOT NULL
              AND lon IS NOT NULL AND lon BETWEEN -180 AND 180
              AND lat IS NOT NULL AND lat BETWEEN -90 AND 90
              AND value IS NOT NULL
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE VIEW resampled AS
            SELECT
              CAST(date_trunc('day', ts) AS DATE) AS date,
              station_id,
              CAST(avg(lon) AS DOUBLE) AS lon,
              CAST(avg(lat) AS DOUBLE) AS lat,
              CAST(avg(value) AS DOUBLE) AS value,
              CAST(count(*) AS BIGINT) AS n_obs
            FROM usable
            GROUP BY 1, 2
            ORDER BY 1, 2
            """
        )
        rows_read = int(connection.execute("SELECT count(*) FROM staged").fetchone()[0])
        rows_kept = int(connection.execute("SELECT count(*) FROM usable").fetchone()[0])
        rows_written = int(connection.execute("SELECT count(*) FROM resampled").fetchone()[0])
        if tmp_path.exists():
            tmp_path.unlink()
        connection.sql("SELECT * FROM resampled").write_parquet(str(tmp_path))
    finally:
        connection.close()

    tmp_path.replace(output_path)
    digest, checksum_path = write_sha256(output_path)
    LOGGER.info(
        "wrote %s (%s rows from %s usable / %s staged)",
        output_path,
        rows_written,
        rows_kept,
        rows_read,
    )
    return BatchResult(
        output_path=output_path,
        checksum_path=checksum_path,
        sha256=digest,
        input_files=tuple(input_files),
        rows_read=rows_read,
        rows_kept=rows_kept,
        rows_written=rows_written,
        resample_rule=rule,
        output_format=fmt,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ecochronos-vault-batch",
        description=(
            "Clean staged station CSVs, resample to a daily station mean, "
            "and write Parquet plus a sha256 sidecar."
        ),
    )
    parser.add_argument(
        "--staging-dir",
        type=Path,
        default=None,
        help="Directory of input CSVs (default: STAGING_DIR or data/staging)",
    )
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=None,
        help="Directory for Parquet output (default: PROCESSED_DIR or data/processed)",
    )
    parser.add_argument(
        "--resample-rule",
        default=None,
        help=f"Resample contract (default: {RESAMPLE_DAILY_MEAN_BY_STATION})",
    )
    parser.add_argument(
        "--output-format",
        default=None,
        help=f"Artifact format (default: {OUTPUT_PARQUET})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    get_settings.cache_clear()
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    try:
        result = run_batch(
            settings,
            staging_dir=args.staging_dir,
            processed_dir=args.processed_dir,
            resample_rule=args.resample_rule,
            output_format=args.output_format,
        )
    except BatchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
