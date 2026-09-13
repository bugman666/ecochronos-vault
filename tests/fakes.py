"""In-memory archive used by HTTP tests so CI does not need a live MinIO."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from typing import BinaryIO

from ecochronos_vault.archive import InvalidObjectKey, ObjectMeta, ObjectNotFound, normalize_object_key


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
