import pytest
from fastapi.testclient import TestClient

from ecochronos_vault.app import create_app
from ecochronos_vault.config import Settings
from tests.fakes import InMemoryArchiveStore

PAYLOAD = b"abcdefghijklmnopqrstuvwxyz"


@pytest.fixture
def archive_store() -> InMemoryArchiveStore:
    store = InMemoryArchiveStore()
    store.put_bytes("demo/alphabet.txt", PAYLOAD, content_type="text/plain")
    return store


@pytest.fixture
def archive_client(settings: Settings, archive_store: InMemoryArchiveStore) -> TestClient:
    return TestClient(create_app(settings, archive_store=archive_store))


def test_full_get_returns_object_and_accept_ranges(
    archive_client: TestClient,
) -> None:
    response = archive_client.get("/archive/demo/alphabet.txt")
    assert response.status_code == 200
    assert response.content == PAYLOAD
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == str(len(PAYLOAD))
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["etag"] == f'"mem-{len(PAYLOAD)}"'


def test_head_returns_size_without_body(archive_client: TestClient) -> None:
    response = archive_client.head("/archive/demo/alphabet.txt")
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == str(len(PAYLOAD))


def test_range_happy_path_returns_partial_content(archive_client: TestClient) -> None:
    response = archive_client.get(
        "/archive/demo/alphabet.txt",
        headers={"Range": "bytes=0-3"},
    )
    assert response.status_code == 206
    assert response.content == b"abcd"
    assert response.headers["content-range"] == f"bytes 0-3/{len(PAYLOAD)}"
    assert response.headers["content-length"] == "4"
    assert response.headers["accept-ranges"] == "bytes"


def test_open_ended_and_suffix_ranges(archive_client: TestClient) -> None:
    tail = archive_client.get(
        "/archive/demo/alphabet.txt",
        headers={"Range": "bytes=23-"},
    )
    assert tail.status_code == 206
    assert tail.content == b"xyz"
    assert tail.headers["content-range"] == f"bytes 23-25/{len(PAYLOAD)}"

    suffix = archive_client.get(
        "/archive/demo/alphabet.txt",
        headers={"Range": "bytes=-3"},
    )
    assert suffix.status_code == 206
    assert suffix.content == b"xyz"


def test_missing_object_is_404(archive_client: TestClient) -> None:
    response = archive_client.get("/archive/demo/missing.bin")
    assert response.status_code == 404
    assert response.json()["detail"] == "object not found"


def test_unsatisfiable_range_is_416(archive_client: TestClient) -> None:
    response = archive_client.get(
        "/archive/demo/alphabet.txt",
        headers={"Range": "bytes=100-200"},
    )
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(PAYLOAD)}"
    assert response.headers["accept-ranges"] == "bytes"


def test_invalid_key_and_multipart_range_are_400(archive_client: TestClient) -> None:
    bad_key = archive_client.get("/archive/demo/../secret")
    assert bad_key.status_code == 400

    multipart = archive_client.get(
        "/archive/demo/alphabet.txt",
        headers={"Range": "bytes=0-1,2-3"},
    )
    assert multipart.status_code == 400


def test_download_without_store_is_503(settings: Settings) -> None:
    response = TestClient(create_app(settings)).get("/archive/demo/alphabet.txt")
    assert response.status_code == 503
    assert response.json()["detail"] == "archive storage is not configured"


def test_put_disabled_without_token(
    settings: Settings, archive_store: InMemoryArchiveStore
) -> None:
    client = TestClient(create_app(settings, archive_store=archive_store))
    response = client.put("/archive/demo/new.txt", content=b"nope")
    assert response.status_code == 403
    assert "demo/new.txt" not in archive_store.objects


def test_put_requires_matching_token(
    settings: Settings, archive_store: InMemoryArchiveStore
) -> None:
    settings.archive_upload_token = "upload-secret"
    client = TestClient(create_app(settings, archive_store=archive_store))

    denied = client.put("/archive/demo/new.txt", content=b"nope")
    assert denied.status_code == 401

    created = client.put(
        "/archive/demo/new.txt",
        content=b"hello-archive",
        headers={
            "Authorization": "Bearer upload-secret",
            "Content-Type": "text/plain",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["key"] == "demo/new.txt"
    assert body["size"] == 13
    fetched = client.get("/archive/demo/new.txt")
    assert fetched.status_code == 200
    assert fetched.content == b"hello-archive"


def test_put_accepts_x_archive_token_header(
    settings: Settings, archive_store: InMemoryArchiveStore
) -> None:
    settings.archive_upload_token = "upload-secret"
    client = TestClient(create_app(settings, archive_store=archive_store))
    response = client.put(
        "/archive/nested/file.bin",
        content=b"xyz",
        headers={"X-Archive-Token": "upload-secret"},
    )
    assert response.status_code == 201
    assert archive_store.objects["nested/file.bin"][0] == b"xyz"
