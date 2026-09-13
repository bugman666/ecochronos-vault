from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from ecochronos_vault.app import create_app
from ecochronos_vault.config import Settings
from ecochronos_vault.metadata import sha256_hex
from tests.fakes import InMemoryMetadataStore

UTC = timezone.utc
DIGEST = sha256_hex(b"chunk-bytes")
OTHER = sha256_hex(b"other-chunk")


@pytest.fixture
def metadata_store() -> InMemoryMetadataStore:
    return InMemoryMetadataStore()


@pytest.fixture
def metadata_client(settings: Settings, metadata_store: InMemoryMetadataStore) -> TestClient:
    settings.archive_upload_token = "upload-secret"
    return TestClient(create_app(settings, metadata_store=metadata_store))


def _payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "chunk_id": "openaq/2024-06-01",
        "storage_key": "archive/openaq/2024-06-01.parquet",
        "checksum": DIGEST,
        "time_start": "2024-06-01T00:00:00Z",
        "time_end": "2024-06-02T00:00:00Z",
        "bbox": [-10.0, 50.0, 2.0, 60.0],
        "dataset": "openaq",
        "size_bytes": 12,
        "content_type": "application/vnd.apache.parquet",
    }
    body.update(overrides)
    return body


def test_chunks_without_store_is_503(settings: Settings) -> None:
    response = TestClient(create_app(settings)).get("/chunks")
    assert response.status_code == 503
    assert response.json()["detail"] == "metadata index is not configured"


def test_put_disabled_without_token(settings: Settings, metadata_store: InMemoryMetadataStore) -> None:
    client = TestClient(create_app(settings, metadata_store=metadata_store))
    response = client.put("/chunks", json=_payload())
    assert response.status_code == 403
    assert metadata_store.chunks == {}


def test_put_requires_matching_token(metadata_client: TestClient, metadata_store: InMemoryMetadataStore) -> None:
    denied = metadata_client.put("/chunks", json=_payload())
    assert denied.status_code == 401

    created = metadata_client.put(
        "/chunks",
        json=_payload(),
        headers={"Authorization": "Bearer upload-secret"},
    )
    assert created.status_code == 201
    body = created.json()
    assert body["chunk_id"] == "openaq/2024-06-01"
    assert body["checksum"] == DIGEST
    assert body["bbox"] == [-10.0, 50.0, 2.0, 60.0]
    assert body["storage_key"] == "archive/openaq/2024-06-01.parquet"
    assert "openaq/2024-06-01" in metadata_store.chunks


def test_put_accepts_x_archive_token_and_upserts(metadata_client: TestClient) -> None:
    headers = {"X-Archive-Token": "upload-secret"}
    first = metadata_client.put("/chunks", json=_payload(), headers=headers)
    assert first.status_code == 201
    second = metadata_client.put(
        "/chunks",
        json=_payload(checksum=OTHER, size_bytes=99),
        headers=headers,
    )
    assert second.status_code == 201
    assert second.json()["checksum"] == OTHER
    fetched = metadata_client.get("/chunks/openaq/2024-06-01")
    assert fetched.status_code == 200
    assert fetched.json()["size_bytes"] == 99


def test_get_missing_chunk_is_404(metadata_client: TestClient) -> None:
    response = metadata_client.get("/chunks/missing/id")
    assert response.status_code == 404
    assert response.json()["detail"] == "chunk not found"


def test_list_filters_time_bbox_prefix_dataset(metadata_client: TestClient) -> None:
    headers = {"Authorization": "Bearer upload-secret"}
    metadata_client.put("/chunks", json=_payload(), headers=headers)
    metadata_client.put(
        "/chunks",
        json=_payload(
            chunk_id="noaa/2024-06-01",
            storage_key="archive/noaa/2024-06-01.nc",
            checksum=OTHER,
            dataset="noaa",
            bbox=[20.0, -10.0, 30.0, 0.0],
        ),
        headers=headers,
    )

    all_chunks = metadata_client.get("/chunks")
    assert all_chunks.status_code == 200
    assert all_chunks.json()["count"] == 2

    by_prefix = metadata_client.get("/chunks", params={"prefix": "archive/openaq"})
    assert [c["chunk_id"] for c in by_prefix.json()["chunks"]] == ["openaq/2024-06-01"]

    by_bbox = metadata_client.get("/chunks", params={"bbox": "-1,54,1,56"})
    assert [c["chunk_id"] for c in by_bbox.json()["chunks"]] == ["openaq/2024-06-01"]

    by_dataset = metadata_client.get("/chunks", params={"dataset": "noaa"})
    assert [c["chunk_id"] for c in by_dataset.json()["chunks"]] == ["noaa/2024-06-01"]

    by_time = metadata_client.get(
        "/chunks",
        params={"time_start": "2024-06-01T00:00:00Z", "time_end": "2024-06-01T12:00:00Z"},
    )
    assert by_time.json()["count"] == 2

    none = metadata_client.get(
        "/chunks",
        params={"time_start": "2025-01-01T00:00:00Z", "time_end": "2025-01-02T00:00:00Z"},
    )
    assert none.json() == {"count": 0, "chunks": []}


def test_invalid_bbox_and_checksum_are_400(metadata_client: TestClient) -> None:
    listed = metadata_client.get("/chunks", params={"bbox": "1,2,3"})
    assert listed.status_code == 400

    created = metadata_client.put(
        "/chunks",
        json=_payload(checksum="nope"),
        headers={"Authorization": "Bearer upload-secret"},
    )
    assert created.status_code == 400


def test_create_app_wires_injected_metadata_store(
    settings: Settings, metadata_store: InMemoryMetadataStore
) -> None:
    app = create_app(settings, metadata_store=metadata_store)
    assert app.state.metadata_store is metadata_store
