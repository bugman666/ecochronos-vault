"""MinIO-backed object archive: put, stat, and ranged get."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import BinaryIO

import urllib3
from minio import Minio
from minio.error import S3Error

from ecochronos_vault.config import Settings

DEFAULT_CHUNK_SIZE = 1024 * 1024
_MAX_KEY_LENGTH = 1024
_MISSING_CODES = frozenset({"NoSuchKey", "NoSuchObject", "NoSuchBucket"})


class ArchiveError(Exception):
    """Base error for archive operations."""


class ArchiveNotConfigured(ArchiveError):
    """MinIO settings are missing, so the archive client cannot be built."""


class ObjectNotFound(ArchiveError):
    """Requested object does not exist in the configured bucket."""

    def __init__(self, key: str) -> None:
        super().__init__(f"object not found: {key}")
        self.key = key


class InvalidObjectKey(ArchiveError, ValueError):
    """Object key is empty, too long, or contains path traversal."""


@dataclass(frozen=True)
class ObjectMeta:
    key: str
    size: int
    etag: str | None
    content_type: str
    last_modified: datetime | None = None


def minio_configured(settings: Settings) -> bool:
    return bool(
        settings.minio_endpoint
        and settings.minio_endpoint.strip()
        and settings.minio_access_key
        and settings.minio_access_key.strip()
        and settings.minio_secret_key
        and settings.minio_secret_key.strip()
    )


def build_minio_client(
    settings: Settings,
    *,
    timeout: urllib3.Timeout | None = None,
    retries: urllib3.Retry | bool | None = None,
) -> Minio:
    """Build a Minio client from process settings."""
    if not minio_configured(settings):
        raise ArchiveNotConfigured("MinIO is not configured")

    kwargs: dict[str, object] = {}
    if timeout is not None or retries is not None:
        pool_kwargs: dict[str, object] = {}
        if timeout is not None:
            pool_kwargs["timeout"] = timeout
        if retries is not None:
            pool_kwargs["retries"] = retries
        kwargs["http_client"] = urllib3.PoolManager(**pool_kwargs)

    return Minio(
        settings.minio_endpoint.strip(),  # type: ignore[union-attr]
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        **kwargs,
    )


def normalize_object_key(key: str) -> str:
    """Reject empty keys and `..` segments; strip a leading slash."""
    key = key.strip().lstrip("/")
    if not key:
        raise InvalidObjectKey("object key is required")
    if len(key) > _MAX_KEY_LENGTH:
        raise InvalidObjectKey("object key is too long")
    if "\x00" in key:
        raise InvalidObjectKey("object key is invalid")
    parts = key.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise InvalidObjectKey("object key is invalid")
    return key


def _is_missing(exc: S3Error) -> bool:
    return exc.code in _MISSING_CODES


def _content_type(value: str | None) -> str:
    return value or "application/octet-stream"


class ArchiveStore:
    """Thin wrapper around a MinIO/S3 bucket used as the public archive."""

    def __init__(
        self,
        client: Minio,
        bucket: str,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        if not bucket or not bucket.strip():
            raise ArchiveError("MinIO bucket is required")
        self._client = client
        self._bucket = bucket.strip()
        self._chunk_size = chunk_size

    @classmethod
    def from_settings(cls, settings: Settings) -> ArchiveStore:
        return cls(build_minio_client(settings), settings.minio_bucket)

    @property
    def bucket(self) -> str:
        return self._bucket

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> ObjectMeta:
        return self.put_stream(
            key,
            BytesIO(data),
            len(data),
            content_type=content_type,
        )

    def put_stream(
        self,
        key: str,
        data: BinaryIO,
        length: int,
        content_type: str = "application/octet-stream",
    ) -> ObjectMeta:
        key = normalize_object_key(key)
        if length < 0:
            raise ArchiveError("object length must be >= 0")
        self.ensure_bucket()
        result = self._client.put_object(
            self._bucket,
            key,
            data,
            length,
            content_type=_content_type(content_type),
        )
        return ObjectMeta(
            key=key,
            size=length,
            etag=getattr(result, "etag", None),
            content_type=_content_type(content_type),
        )

    def stat(self, key: str) -> ObjectMeta:
        key = normalize_object_key(key)
        try:
            info = self._client.stat_object(self._bucket, key)
        except S3Error as exc:
            if _is_missing(exc):
                raise ObjectNotFound(key) from exc
            raise
        return ObjectMeta(
            key=key,
            size=int(info.size or 0),
            etag=getattr(info, "etag", None),
            content_type=_content_type(getattr(info, "content_type", None)),
            last_modified=getattr(info, "last_modified", None),
        )

    def iter_object(
        self,
        key: str,
        *,
        offset: int = 0,
        length: int | None = None,
        chunk_size: int | None = None,
    ) -> Iterator[bytes]:
        """Yield object bytes, optionally a `[offset, offset+length)` slice."""
        key = normalize_object_key(key)
        if offset < 0:
            raise ArchiveError("offset must be >= 0")
        if length is not None and length < 0:
            raise ArchiveError("length must be >= 0")
        if length == 0:
            return
            yield  # pragma: no cover - makes this a generator

        kwargs: dict[str, int] = {"offset": offset}
        if length is not None:
            kwargs["length"] = length

        response = None
        try:
            response = self._client.get_object(self._bucket, key, **kwargs)
            yield from response.stream(chunk_size or self._chunk_size)
        except S3Error as exc:
            if _is_missing(exc):
                raise ObjectNotFound(key) from exc
            raise
        finally:
            if response is not None:
                response.close()
                response.release_conn()


def archive_store_from_settings(settings: Settings) -> ArchiveStore | None:
    if not minio_configured(settings):
        return None
    return ArchiveStore.from_settings(settings)
