from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from psycopg.errors import UniqueViolation

from ecochronos_vault.config import Settings
from ecochronos_vault.metadata import (
    ChunkQuery,
    ChunkWrite,
    InvalidBBox,
    InvalidChunk,
    PgMetadataStore,
    bbox_intersects,
    escape_like_prefix,
    metadata_store_from_settings,
    normalize_bbox,
    normalize_checksum,
    normalize_chunk_id,
    parse_bbox_csv,
    sha256_hex,
    time_overlaps,
)
from ecochronos_vault.migrate import load_migration_sql, split_sql_statements
from tests.fakes import InMemoryMetadataStore

UTC = timezone.utc
DAY = datetime(2024, 6, 1, tzinfo=UTC)
NEXT = datetime(2024, 6, 2, tzinfo=UTC)
DIGEST = sha256_hex(b"chunk-bytes")


def _write(**overrides: object) -> ChunkWrite:
    payload = dict(
        chunk_id="openaq/2024-06-01",
        storage_key="archive/openaq/2024-06-01.parquet",
        checksum=DIGEST,
        time_start=DAY,
        time_end=NEXT,
        bbox=(-10.0, 50.0, 2.0, 60.0),
        dataset="openaq",
        size_bytes=12,
        content_type="application/vnd.apache.parquet",
    )
    payload.update(overrides)
    return ChunkWrite(**payload)  # type: ignore[arg-type]


def test_sha256_hex_and_normalize() -> None:
    assert len(DIGEST) == 64
    assert normalize_checksum(DIGEST.upper()) == (DIGEST, "sha256")
    with pytest.raises(InvalidChunk):
        normalize_checksum("not-a-hash")
    with pytest.raises(InvalidChunk):
        normalize_checksum(DIGEST, "md5")


def test_normalize_chunk_id_and_bbox() -> None:
    assert normalize_chunk_id("/demo/a") == "demo/a"
    with pytest.raises(InvalidChunk):
        normalize_chunk_id("../secret")
    assert normalize_bbox((-10, 50, 2, 60)) == (-10.0, 50.0, 2.0, 60.0)
    assert parse_bbox_csv("-10,50,2,60") == (-10.0, 50.0, 2.0, 60.0)
    with pytest.raises(InvalidBBox):
        normalize_bbox((10, 50, 2, 60))
    with pytest.raises(InvalidBBox):
        parse_bbox_csv("1,2,3")


def test_time_overlap_and_bbox_intersect() -> None:
    assert time_overlaps(DAY, NEXT, DAY, NEXT)
    assert time_overlaps(DAY, NEXT, NEXT, datetime(2024, 6, 3, tzinfo=UTC))
    assert not time_overlaps(DAY, NEXT, datetime(2024, 6, 3, tzinfo=UTC), datetime(2024, 6, 4, tzinfo=UTC))
    assert bbox_intersects((-10, 50, 2, 60), (0, 55, 5, 65))
    assert not bbox_intersects((-10, 50, 2, 60), (3, 50, 5, 60))


def test_escape_like_prefix() -> None:
    assert escape_like_prefix("a%b_c") == "a\\%b\\_c"


def test_chunk_write_rejects_inverted_time() -> None:
    with pytest.raises(InvalidChunk):
        _write(time_start=NEXT, time_end=DAY)


def test_metadata_store_from_settings(settings: Settings) -> None:
    assert metadata_store_from_settings(settings) is None
    settings.postgres_dsn = "postgresql://ecochronos:x@127.0.0.1:5432/ecochronos"
    store = metadata_store_from_settings(settings)
    assert isinstance(store, PgMetadataStore)


def test_inmemory_upsert_get_and_search() -> None:
    store = InMemoryMetadataStore()
    stored = store.upsert(_write())
    assert store.get("openaq/2024-06-01").checksum == DIGEST
    assert stored.storage_key == "archive/openaq/2024-06-01.parquet"

    store.upsert(
        _write(
            chunk_id="noaa/2024-06-01",
            storage_key="archive/noaa/2024-06-01.nc",
            dataset="noaa",
            bbox=(20.0, -10.0, 30.0, 0.0),
        )
    )

    by_prefix = store.search(ChunkQuery(prefix="archive/openaq"))
    assert [c.chunk_id for c in by_prefix] == ["openaq/2024-06-01"]

    by_bbox = store.search(ChunkQuery(bbox=(-1.0, 54.0, 1.0, 56.0)))
    assert [c.chunk_id for c in by_bbox] == ["openaq/2024-06-01"]

    by_dataset = store.search(ChunkQuery(dataset="noaa"))
    assert [c.chunk_id for c in by_dataset] == ["noaa/2024-06-01"]

    outside = store.search(
        ChunkQuery(time_start=datetime(2025, 1, 1, tzinfo=UTC), time_end=datetime(2025, 1, 2, tzinfo=UTC))
    )
    assert outside == []


def test_inmemory_rejects_duplicate_storage_key() -> None:
    store = InMemoryMetadataStore()
    store.upsert(_write())
    with pytest.raises(InvalidChunk, match="storage_key already registered"):
        store.upsert(_write(chunk_id="other/id"))


def test_pg_upsert_maps_unique_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = MagicMock()
    conn.execute.side_effect = UniqueViolation("duplicate")
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    monkeypatch.setattr("ecochronos_vault.metadata.connect", lambda *a, **k: conn)
    store = PgMetadataStore("postgresql://example", apply_schema=False)
    with pytest.raises(InvalidChunk, match="storage_key already registered"):
        store.upsert(_write())


def test_inmemory_upsert_replaces_same_id() -> None:
    store = InMemoryMetadataStore()
    store.upsert(_write())
    other = sha256_hex(b"replaced")
    updated = store.upsert(_write(checksum=other, size_bytes=99))
    assert updated.checksum == other
    assert store.get("openaq/2024-06-01").size_bytes == 99


def test_schema_sql_declares_postgis_index_and_checksum() -> None:
    sql = load_migration_sql("001_chunk_metadata.sql")
    assert "CREATE EXTENSION IF NOT EXISTS postgis" in sql
    assert "geometry(Polygon, 4326)" in sql
    assert "USING GIST (extent)" in sql
    assert "checksum" in sql
    statements = split_sql_statements(sql)
    assert any("CREATE TABLE IF NOT EXISTS chunk_metadata" in s for s in statements)
    assert any("GIST" in s for s in statements)


def test_pg_upsert_uses_envelope_and_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = MagicMock()
    cursor = MagicMock()
    west, south, east, north = (-10.0, 50.0, 2.0, 60.0)
    cursor.fetchone.return_value = (
        "openaq/2024-06-01",
        "archive/openaq/2024-06-01.parquet",
        None,
        "openaq",
        DIGEST,
        "sha256",
        DAY,
        NEXT,
        west,
        south,
        east,
        north,
        12,
        "application/vnd.apache.parquet",
        DAY,
        DAY,
    )
    conn.execute.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    monkeypatch.setattr("ecochronos_vault.metadata.connect", lambda *a, **k: conn)

    store = PgMetadataStore("postgresql://example", apply_schema=False)
    record = store.upsert(_write())
    assert record.chunk_id == "openaq/2024-06-01"
    sql = conn.execute.call_args_list[-1].args[0]
    assert "ST_MakeEnvelope" in sql
    assert "ON CONFLICT (chunk_id)" in sql


def test_pg_search_uses_intersects_and_like(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn.execute.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    monkeypatch.setattr("ecochronos_vault.metadata.connect", lambda *a, **k: conn)

    store = PgMetadataStore("postgresql://example", apply_schema=False)
    store.search(
        ChunkQuery(
            time_start=DAY,
            time_end=NEXT,
            bbox=(-10.0, 50.0, 2.0, 60.0),
            prefix="archive/openaq",
            dataset="openaq",
        )
    )
    sql, params = conn.execute.call_args.args
    assert "ST_Intersects" in sql
    assert "ST_MakeEnvelope" in sql
    assert "starts_with" in sql
    assert params[0] == DAY
    assert params[1] == NEXT
    assert list(params[2:6]) == [-10.0, 50.0, 2.0, 60.0]
    assert params[6] == "archive/openaq"
    assert params[7] == "openaq"
