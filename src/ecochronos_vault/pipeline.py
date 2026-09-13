"""Later pipeline stages. Placeholders for issues #3 and #5."""

from pathlib import Path


def write_parquet_batch(raw_path: Path, output_dir: Path) -> Path:
    """Clean and resample a raw ingest into Parquet (or NetCDF/Zarr).

    TODO(#3): local batch transform with DuckDB (or similar).
    """
    raise NotImplementedError("Parquet batch is not implemented yet (issue #3)")


def register_postgis_metadata(local_path: Path, checksum: str) -> None:
    """Index a granule (path, time range, bbox, checksum) in PostGIS.

    TODO(#5): metadata registration and a simple query API.
    """
    raise NotImplementedError("PostGIS metadata is not implemented yet (issue #5)")
