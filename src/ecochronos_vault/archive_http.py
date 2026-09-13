"""Read-mostly HTTP surface for archived objects, with Range resume."""

from __future__ import annotations

import hmac
import tempfile
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ecochronos_vault.archive import (
    ArchiveStore,
    InvalidObjectKey,
    ObjectMeta,
    ObjectNotFound,
    normalize_object_key,
)
from ecochronos_vault.config import Settings
from ecochronos_vault.ranges import InvalidRangeHeader, UnsatisfiableRange, parse_byte_range

router = APIRouter(tags=["archive"])

_SPOOL_THRESHOLD = 8 * 1024 * 1024


def get_archive_store(request: Request) -> ArchiveStore:
    store = getattr(request.app.state, "archive_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="archive storage is not configured")
    return store


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _safe_key(object_key: str) -> str:
    try:
        return normalize_object_key(object_key)
    except InvalidObjectKey as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _quote_etag(etag: str | None) -> str | None:
    if not etag:
        return None
    value = etag.strip()
    if value.startswith("W/"):
        return value
    if value.startswith('"') and value.endswith('"'):
        return value
    return f'"{value}"'


def _last_modified(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return format_datetime(value.astimezone(timezone.utc), usegmt=True)


def _content_headers(meta: ObjectMeta) -> dict[str, str]:
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": meta.content_type,
    }
    etag = _quote_etag(meta.etag)
    if etag:
        headers["ETag"] = etag
    last_modified = _last_modified(meta.last_modified)
    if last_modified:
        headers["Last-Modified"] = last_modified
    filename = meta.key.rsplit("/", 1)[-1]
    if filename:
        headers["Content-Disposition"] = f'inline; filename="{filename}"'
    return headers


def _stat_or_404(store: ArchiveStore, key: str) -> ObjectMeta:
    try:
        return store.stat(key)
    except ObjectNotFound as exc:
        raise HTTPException(status_code=404, detail="object not found") from exc


def _upload_token_ok(settings: Settings, authorization: str | None, x_archive_token: str | None) -> bool:
    expected = (settings.archive_upload_token or "").strip()
    if not expected:
        return False
    provided = ""
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    elif x_archive_token:
        provided = x_archive_token.strip()
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


@router.head("/archive/{object_key:path}")
def head_archive(object_key: str, request: Request) -> Response:
    """Object metadata for clients that want size before a ranged GET."""
    key = _safe_key(object_key)
    meta = _stat_or_404(get_archive_store(request), key)
    headers = _content_headers(meta)
    headers["Content-Length"] = str(meta.size)
    return Response(status_code=200, headers=headers)


@router.get("/archive/{object_key:path}")
def download_archive(
    object_key: str,
    request: Request,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
) -> Response:
    """Full or partial download. Honors a single `Range: bytes=` request."""
    key = _safe_key(object_key)
    store = get_archive_store(request)
    meta = _stat_or_404(store, key)

    try:
        byte_range = parse_byte_range(range_header, meta.size)
    except InvalidRangeHeader as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UnsatisfiableRange:
        return Response(
            status_code=416,
            headers={
                "Accept-Ranges": "bytes",
                "Content-Range": f"bytes */{meta.size}",
            },
        )

    if byte_range is None:
        start, length, status = 0, meta.size, 200
        extra: dict[str, str] = {}
    else:
        start, length, status = byte_range.start, byte_range.length, 206
        extra = {"Content-Range": byte_range.content_range}

    headers = _content_headers(meta)
    headers["Content-Length"] = str(length)
    headers.update(extra)

    if length == 0:
        return Response(content=b"", status_code=status, headers=headers)

    return StreamingResponse(
        store.iter_object(key, offset=start, length=length),
        status_code=status,
        media_type=meta.content_type,
        headers=headers,
    )


@router.put("/archive/{object_key:path}")
async def upload_archive(
    object_key: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_archive_token: Annotated[str | None, Header(alias="X-Archive-Token")] = None,
) -> JSONResponse:
    """Optional ingest path. Disabled unless `ARCHIVE_UPLOAD_TOKEN` is set."""
    settings = _settings(request)
    expected = (settings.archive_upload_token or "").strip()
    if not expected:
        raise HTTPException(status_code=403, detail="archive upload is disabled")
    if not _upload_token_ok(settings, authorization, x_archive_token):
        raise HTTPException(status_code=401, detail="invalid or missing upload token")

    key = _safe_key(object_key)
    store = get_archive_store(request)
    content_type = request.headers.get("content-type") or "application/octet-stream"

    with tempfile.SpooledTemporaryFile(max_size=_SPOOL_THRESHOLD) as tmp:
        async for chunk in request.stream():
            tmp.write(chunk)
        length = tmp.tell()
        tmp.seek(0)
        meta = store.put_stream(key, tmp, length, content_type=content_type)

    return JSONResponse(
        status_code=201,
        content={
            "key": meta.key,
            "size": meta.size,
            "etag": meta.etag,
            "content_type": meta.content_type,
        },
    )
