"""Chunk metadata: checksum + spatial/temporal index (Postgres+PostGIS).

Ingest and batch jobs can register a chunk after it lands in the archive
without depending on this module's HTTP layer:

    store = metadata_store_from_settings(get_settings())
    store.upsert(ChunkWrite(...))
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from psycopg import connect
from psycopg.errors import UniqueViolation
from psycopg.rows import tuple_row

from ecochronos_vault.archive import InvalidObjectKey, normalize_object_key
from ecochronos_vault.config import Settings
from ecochronos_vault.migrate import apply_migrations_on_connection

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_CHUNK_ID = 512
_MAX_URI = 2048
_MAX_DATASET = 128
_MAX_CONTENT_TYPE = 256
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500

_SELECT_COLS = """
    chunk_id,
    storage_key,
    uri,
    dataset,
    checksum,
    checksum_alg,
    time_start,
    time_end,
    ST_XMin(extent),
    ST_YMin(extent),
    ST_XMax(extent),
    ST_YMax(extent),
    size_bytes,
    content_type,
    created_at,
    updated_at
"""


class MetadataError(Exception):
    """Base error for metadata operations."""


class MetadataNotConfigured(MetadataError):
    """Postgres DSN is missing, so the metadata index cannot be used."""


class ChunkNotFound(MetadataError):
    def __init__(self, chunk_id: str) -> None:
        super().__init__(f"chunk not found: {chunk_id}")
        self.chunk_id = chunk_id


class InvalidChunk(MetadataError, ValueError):
    """Chunk payload or query is invalid."""


class InvalidBBox(InvalidChunk):
    """Bounding box is missing a coordinate or is not a valid WGS84 envelope."""


BBox = tuple[float, float, float, float]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_chunk_id(chunk_id: str) -> str:
    """Stable caller-supplied identity. Same path rules as archive keys."""
    try:
        value = normalize_object_key(chunk_id)
    except InvalidObjectKey as exc:
        raise InvalidChunk(str(exc)) from exc
    if len(value) > _MAX_CHUNK_ID:
        raise InvalidChunk("chunk_id is too long")
    return value


def normalize_bbox(value: Sequence[float] | BBox) -> BBox:
    if len(value) != 4:
        raise InvalidBBox("bbox must be [west, south, east, north]")
    try:
        west, south, east, north = (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    except (TypeError, ValueError) as exc:
        raise InvalidBBox("bbox values must be numbers") from exc
    if any(v != v or v in (float("inf"), float("-inf")) for v in (west, south, east, north)):
        raise InvalidBBox("bbox values must be finite")
    if not -180.0 <= west <= 180.0 or not -180.0 <= east <= 180.0:
        raise InvalidBBox("bbox longitude must be in [-180, 180]")
    if not -90.0 <= south <= 90.0 or not -90.0 <= north <= 90.0:
        raise InvalidBBox("bbox latitude must be in [-90, 90]")
    if west >= east or south >= north:
        raise InvalidBBox("bbox must have west < east and south < north")
    return (west, south, east, north)


def parse_bbox_csv(raw: str) -> BBox:
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) != 4:
        raise InvalidBBox("bbox must be west,south,east,north")
    try:
        nums = tuple(float(p) for p in parts)
    except ValueError as exc:
        raise InvalidBBox("bbox values must be numbers") from exc
    return normalize_bbox(nums)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_checksum(checksum: str, algorithm: str = "sha256") -> tuple[str, str]:
    alg = (algorithm or "sha256").strip().lower()
    if alg != "sha256":
        raise InvalidChunk("only sha256 checksums are supported")
    hex_digest = checksum.strip().lower()
    if not SHA256_RE.fullmatch(hex_digest):
        raise InvalidChunk("checksum must be a 64-character sha256 hex digest")
    return hex_digest, alg


def bbox_intersects(a: BBox, b: BBox) -> bool:
    aw, asouth, ae, an = a
    bw, bsouth, be, bn = b
    return aw < be and ae > bw and asouth < bn and an > bsouth


def time_overlaps(
    start: datetime,
    end: datetime,
    query_start: datetime | None,
    query_end: datetime | None,
) -> bool:
    if query_start is not None and end < query_start:
        return False
    if query_end is not None and start > query_end:
        return False
    return True


def escape_like_prefix(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _optional_text(value: str | None, *, field: str, max_len: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if "\x00" in text:
        raise InvalidChunk(f"{field} is invalid")
    if len(text) > max_len:
        raise InvalidChunk(f"{field} is too long")
    return text


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ChunkWrite:
    chunk_id: str
    storage_key: str
    checksum: str
    time_start: datetime
    time_end: datetime
    bbox: BBox
    uri: str | None = None
    dataset: str | None = None
    checksum_alg: str = "sha256"
    size_bytes: int | None = None
    content_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "chunk_id", normalize_chunk_id(self.chunk_id))
        try:
            object.__setattr__(self, "storage_key", normalize_object_key(self.storage_key))
        except InvalidObjectKey as exc:
            raise InvalidChunk(str(exc)) from exc
        digest, alg = normalize_checksum(self.checksum, self.checksum_alg)
        object.__setattr__(self, "checksum", digest)
        object.__setattr__(self, "checksum_alg", alg)
        start = _as_utc(self.time_start)
        end = _as_utc(self.time_end)
        if end < start:
            raise InvalidChunk("time_end must be >= time_start")
        object.__setattr__(self, "time_start", start)
        object.__setattr__(self, "time_end", end)
        object.__setattr__(self, "bbox", normalize_bbox(self.bbox))
        object.__setattr__(self, "uri", _optional_text(self.uri, field="uri", max_len=_MAX_URI))
        object.__setattr__(
            self, "dataset", _optional_text(self.dataset, field="dataset", max_len=_MAX_DATASET)
        )
        object.__setattr__(
            self,
            "content_type",
            _optional_text(self.content_type, field="content_type", max_len=_MAX_CONTENT_TYPE),
        )
        if self.size_bytes is not None and self.size_bytes < 0:
            raise InvalidChunk("size_bytes must be >= 0")


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    storage_key: str
    checksum: str
    time_start: datetime
    time_end: datetime
    bbox: BBox
    uri: str | None = None
    dataset: str | None = None
    checksum_alg: str = "sha256"
    size_bytes: int | None = None
    content_type: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "chunk_id": self.chunk_id,
            "storage_key": self.storage_key,
            "uri": self.uri,
            "dataset": self.dataset,
            "checksum": self.checksum,
            "checksum_alg": self.checksum_alg,
            "time_start": _iso(self.time_start),
            "time_end": _iso(self.time_end),
            "bbox": list(self.bbox),
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }


@dataclass(frozen=True)
class ChunkQuery:
    time_start: datetime | None = None
    time_end: datetime | None = None
    bbox: BBox | None = None
    prefix: str | None = None
    dataset: str | None = None
    limit: int = _DEFAULT_LIMIT
    offset: int = 0

    def __post_init__(self) -> None:
        start = _as_utc(self.time_start) if self.time_start is not None else None
        end = _as_utc(self.time_end) if self.time_end is not None else None
        if start is not None and end is not None and end < start:
            raise InvalidChunk("time_end must be >= time_start")
        object.__setattr__(self, "time_start", start)
        object.__setattr__(self, "time_end", end)
        if self.bbox is not None:
            object.__setattr__(self, "bbox", normalize_bbox(self.bbox))
        prefix = _optional_text(self.prefix, field="prefix", max_len=_MAX_CHUNK_ID)
        object.__setattr__(self, "prefix", prefix)
        object.__setattr__(
            self, "dataset", _optional_text(self.dataset, field="dataset", max_len=_MAX_DATASET)
        )
        if self.limit < 1 or self.limit > _MAX_LIMIT:
            raise InvalidChunk(f"limit must be between 1 and {_MAX_LIMIT}")
        if self.offset < 0:
            raise InvalidChunk("offset must be >= 0")


class MetadataStore(Protocol):
    def upsert(self, record: ChunkWrite) -> ChunkRecord: ...

    def get(self, chunk_id: str) -> ChunkRecord: ...

    def search(self, query: ChunkQuery) -> list[ChunkRecord]: ...


def _row_to_record(row: tuple[object, ...]) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=str(row[0]),
        storage_key=str(row[1]),
        uri=row[2] if row[2] is None else str(row[2]),
        dataset=row[3] if row[3] is None else str(row[3]),
        checksum=str(row[4]),
        checksum_alg=str(row[5]),
        time_start=_as_utc(row[6]),  # type: ignore[arg-type]
        time_end=_as_utc(row[7]),  # type: ignore[arg-type]
        bbox=(float(row[8]), float(row[9]), float(row[10]), float(row[11])),  # type: ignore[arg-type]
        size_bytes=int(row[12]) if row[12] is not None else None,
        content_type=row[13] if row[13] is None else str(row[13]),
        created_at=_as_utc(row[14]) if row[14] is not None else None,  # type: ignore[arg-type]
        updated_at=_as_utc(row[15]) if row[15] is not None else None,  # type: ignore[arg-type]
    )


class PgMetadataStore:
    """Postgres+PostGIS implementation. Applies schema on first use."""

    def __init__(self, dsn: str, *, apply_schema: bool = True) -> None:
        if not dsn or not dsn.strip():
            raise MetadataNotConfigured("Postgres DSN is required")
        self._dsn = dsn.strip()
        self._apply_schema = apply_schema

    def _connect(self):
        conn = connect(self._dsn, row_factory=tuple_row)
        if self._apply_schema:
            apply_migrations_on_connection(conn)
            conn.commit()
            self._apply_schema = False
        return conn

    def upsert(self, record: ChunkWrite) -> ChunkRecord:
        west, south, east, north = record.bbox
        sql = f"""
            INSERT INTO chunk_metadata (
                chunk_id, storage_key, uri, dataset,
                checksum, checksum_alg,
                time_start, time_end, extent,
                size_bytes, content_type
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, ST_MakeEnvelope(%s, %s, %s, %s, 4326),
                %s, %s
            )
            ON CONFLICT (chunk_id) DO UPDATE SET
                storage_key = EXCLUDED.storage_key,
                uri = EXCLUDED.uri,
                dataset = EXCLUDED.dataset,
                checksum = EXCLUDED.checksum,
                checksum_alg = EXCLUDED.checksum_alg,
                time_start = EXCLUDED.time_start,
                time_end = EXCLUDED.time_end,
                extent = EXCLUDED.extent,
                size_bytes = EXCLUDED.size_bytes,
                content_type = EXCLUDED.content_type,
                updated_at = now()
            RETURNING {_SELECT_COLS}
        """
        params = (
            record.chunk_id,
            record.storage_key,
            record.uri,
            record.dataset,
            record.checksum,
            record.checksum_alg,
            record.time_start,
            record.time_end,
            west,
            south,
            east,
            north,
            record.size_bytes,
            record.content_type,
        )
        try:
            with self._connect() as conn:
                row = conn.execute(sql, params).fetchone()
                conn.commit()
        except UniqueViolation as exc:
            raise InvalidChunk(f"storage_key already registered: {record.storage_key}") from exc
        if row is None:  # pragma: no cover - RETURNING always yields a row
            raise MetadataError("upsert returned no row")
        return _row_to_record(row)

    def get(self, chunk_id: str) -> ChunkRecord:
        chunk_id = normalize_chunk_id(chunk_id)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT {_SELECT_COLS} FROM chunk_metadata WHERE chunk_id = %s",
                (chunk_id,),
            ).fetchone()
        if row is None:
            raise ChunkNotFound(chunk_id)
        return _row_to_record(row)

    def search(self, query: ChunkQuery) -> list[ChunkRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if query.time_start is not None:
            clauses.append("time_end >= %s")
            params.append(query.time_start)
        if query.time_end is not None:
            clauses.append("time_start <= %s")
            params.append(query.time_end)
        if query.bbox is not None:
            west, south, east, north = query.bbox
            clauses.append("ST_Intersects(extent, ST_MakeEnvelope(%s, %s, %s, %s, 4326))")
            params.extend((west, south, east, north))
        if query.prefix is not None:
            clauses.append("starts_with(storage_key, %s)")
            params.append(query.prefix)
        if query.dataset is not None:
            clauses.append("dataset = %s")
            params.append(query.dataset)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT {_SELECT_COLS} FROM chunk_metadata {where} "
            "ORDER BY time_start ASC, chunk_id ASC "
            "LIMIT %s OFFSET %s"
        )
        params.extend((query.limit, query.offset))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_record(row) for row in rows]


def metadata_store_from_settings(settings: Settings) -> PgMetadataStore | None:
    dsn = (settings.postgres_dsn or "").strip()
    if not dsn:
        return None
    return PgMetadataStore(dsn)
