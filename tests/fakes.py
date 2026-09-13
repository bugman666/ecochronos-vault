"""In-memory archive used by HTTP tests so CI does not need a live MinIO."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import BinaryIO

from ecochronos_vault.archive import InvalidObjectKey, ObjectMeta, ObjectNotFound, normalize_object_key
from ecochronos_vault.metadata import (
    ChunkNotFound,
    ChunkQuery,
    ChunkRecord,
    ChunkWrite,
    InvalidChunk,
    bbox_intersects,
    normalize_chunk_id,
    time_overlaps,
)


class InMemoryArchiveStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> ObjectMeta:
        key = normalize_object_key(key)
        self.objects[key] = (data, content_type or "application/octet-stream")
        return ObjectMeta(
            key=key,
            size=len(data),
            etag=f"mem-{len(data)}",
            content_type=content_type or "application/octet-stream",
            last_modified=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )

    def put_stream(
        self,
        key: str,
        data: BinaryIO,
        length: int,
        content_type: str = "application/octet-stream",
    ) -> ObjectMeta:
        return self.put_bytes(key, data.read(length), content_type=content_type)

    def stat(self, key: str) -> ObjectMeta:
        key = normalize_object_key(key)
        try:
            data, content_type = self.objects[key]
        except KeyError as exc:
            raise ObjectNotFound(key) from exc
        return ObjectMeta(
            key=key,
            size=len(data),
            etag=f"mem-{len(data)}",
            content_type=content_type,
            last_modified=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )

    def iter_object(
        self,
        key: str,
        *,
        offset: int = 0,
        length: int | None = None,
        chunk_size: int | None = None,
    ) -> Iterator[bytes]:
        key = normalize_object_key(key)
        try:
            data, _content_type = self.objects[key]
        except KeyError as exc:
            raise ObjectNotFound(key) from exc
        end = len(data) if length is None else offset + length
        chunk = data[offset:end]
        size = chunk_size or 8
        if not chunk:
            return
        for i in range(0, len(chunk), size):
            yield chunk[i : i + size]


class InMemoryMetadataStore:
    """In-process chunk index so CI does not need a live PostGIS."""

    def __init__(self) -> None:
        self.chunks: dict[str, ChunkRecord] = {}

    def upsert(self, record: ChunkWrite) -> ChunkRecord:
        for existing in self.chunks.values():
            if existing.storage_key == record.storage_key and existing.chunk_id != record.chunk_id:
                raise InvalidChunk(f"storage_key already registered: {record.storage_key}")
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        previous = self.chunks.get(record.chunk_id)
        stored = ChunkRecord(
            chunk_id=record.chunk_id,
            storage_key=record.storage_key,
            checksum=record.checksum,
            time_start=record.time_start,
            time_end=record.time_end,
            bbox=record.bbox,
            uri=record.uri,
            dataset=record.dataset,
            checksum_alg=record.checksum_alg,
            size_bytes=record.size_bytes,
            content_type=record.content_type,
            created_at=previous.created_at if previous else now,
            updated_at=now,
        )
        self.chunks[record.chunk_id] = stored
        return stored

    def get(self, chunk_id: str) -> ChunkRecord:
        chunk_id = normalize_chunk_id(chunk_id)
        try:
            return self.chunks[chunk_id]
        except KeyError as exc:
            raise ChunkNotFound(chunk_id) from exc

    def search(self, query: ChunkQuery) -> list[ChunkRecord]:
        matched: list[ChunkRecord] = []
        for record in self.chunks.values():
            if not time_overlaps(record.time_start, record.time_end, query.time_start, query.time_end):
                continue
            if query.bbox is not None and not bbox_intersects(record.bbox, query.bbox):
                continue
            if query.prefix is not None and not record.storage_key.startswith(query.prefix):
                continue
            if query.dataset is not None and record.dataset != query.dataset:
                continue
            matched.append(record)
        matched.sort(key=lambda item: (item.time_start, item.chunk_id))
        return matched[query.offset : query.offset + query.limit]
