"""HTTP surface for chunk metadata: register (write token) and search/list."""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ecochronos_vault.config import Settings
from ecochronos_vault.metadata import (
    BBox,
    ChunkNotFound,
    ChunkQuery,
    ChunkWrite,
    InvalidBBox,
    InvalidChunk,
    MetadataStore,
    parse_bbox_csv,
)

router = APIRouter(tags=["metadata"])


class ChunkIn(BaseModel):
    chunk_id: str
    storage_key: str
    checksum: str
    time_start: datetime
    time_end: datetime
    bbox: list[float] = Field(
        ...,
        min_length=4,
        max_length=4,
        description="[west, south, east, north] in WGS84.",
    )
    uri: str | None = None
    dataset: str | None = None
    checksum_alg: str = "sha256"
    size_bytes: int | None = None
    content_type: str | None = None


def get_metadata_store(request: Request) -> MetadataStore:
    store = getattr(request.app.state, "metadata_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="metadata index is not configured")
    return store


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _write_token_ok(settings: Settings, authorization: str | None, x_archive_token: str | None) -> bool:
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


def _http_invalid(exc: InvalidChunk) -> HTTPException:
    status = 400
    return HTTPException(status_code=status, detail=str(exc))


def _parse_query_time(raw: str | None, field: str) -> datetime | None:
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{field} must be an ISO-8601 datetime") from exc


def _chunk_write(body: ChunkIn) -> ChunkWrite:
    try:
        bbox: BBox = (body.bbox[0], body.bbox[1], body.bbox[2], body.bbox[3])
        return ChunkWrite(
            chunk_id=body.chunk_id,
            storage_key=body.storage_key,
            checksum=body.checksum,
            time_start=body.time_start,
            time_end=body.time_end,
            bbox=bbox,
            uri=body.uri,
            dataset=body.dataset,
            checksum_alg=body.checksum_alg,
            size_bytes=body.size_bytes,
            content_type=body.content_type,
        )
    except InvalidChunk as exc:
        raise _http_invalid(exc) from exc


@router.put("/chunks")
def put_chunk(
    body: ChunkIn,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_archive_token: Annotated[str | None, Header(alias="X-Archive-Token")] = None,
) -> JSONResponse:
    """Insert or replace one chunk. Disabled unless ``ARCHIVE_UPLOAD_TOKEN`` is set."""
    settings = _settings(request)
    if not (settings.archive_upload_token or "").strip():
        raise HTTPException(status_code=403, detail="metadata registration is disabled")
    if not _write_token_ok(settings, authorization, x_archive_token):
        raise HTTPException(status_code=401, detail="invalid or missing upload token")

    record = get_metadata_store(request).upsert(_chunk_write(body))
    return JSONResponse(status_code=201, content=record.to_dict())


@router.get("/chunks")
def list_chunks(
    request: Request,
    time_start: Annotated[str | None, Query()] = None,
    time_end: Annotated[str | None, Query()] = None,
    bbox: Annotated[str | None, Query(description="west,south,east,north (WGS84)")] = None,
    prefix: Annotated[str | None, Query(description="storage_key prefix")] = None,
    dataset: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, object]:
    """List chunks overlapping an optional time range, bbox, key prefix, and dataset."""
    parsed_bbox = None
    if bbox is not None and bbox.strip():
        try:
            parsed_bbox = parse_bbox_csv(bbox)
        except InvalidBBox as exc:
            raise _http_invalid(exc) from exc
    try:
        query = ChunkQuery(
            time_start=_parse_query_time(time_start, "time_start"),
            time_end=_parse_query_time(time_end, "time_end"),
            bbox=parsed_bbox,
            prefix=prefix,
            dataset=dataset,
            limit=limit,
            offset=offset,
        )
    except InvalidChunk as exc:
        raise _http_invalid(exc) from exc
    chunks = get_metadata_store(request).search(query)
    return {"count": len(chunks), "chunks": [chunk.to_dict() for chunk in chunks]}


@router.get("/chunks/{chunk_id:path}")
def get_chunk(chunk_id: str, request: Request) -> dict[str, object]:
    store = get_metadata_store(request)
    try:
        return store.get(chunk_id).to_dict()
    except InvalidChunk as exc:
        raise _http_invalid(exc) from exc
    except ChunkNotFound as exc:
        raise HTTPException(status_code=404, detail="chunk not found") from exc
