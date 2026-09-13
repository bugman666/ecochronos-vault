from io import BytesIO
from unittest.mock import MagicMock

import pytest
from minio.error import S3Error

from ecochronos_vault.archive import (
    ArchiveNotConfigured,
    ArchiveStore,
    InvalidObjectKey,
    ObjectNotFound,
    archive_store_from_settings,
    build_minio_client,
    minio_configured,
    normalize_object_key,
)
from ecochronos_vault.config import Settings


def _s3_error(code: str) -> S3Error:
    return S3Error(
        MagicMock(),
        code,
        "The specified key does not exist.",
        "/ecochronos/missing",
        "req",
        "host",
        "ecochronos",
        "missing",
    )


def test_normalize_object_key_strips_slash_and_rejects_traversal() -> None:
    assert normalize_object_key("/demo/hello.txt") == "demo/hello.txt"
    with pytest.raises(InvalidObjectKey):
        normalize_object_key("")
    with pytest.raises(InvalidObjectKey):
        normalize_object_key("../secret")
    with pytest.raises(InvalidObjectKey):
        normalize_object_key("a//b")
    with pytest.raises(InvalidObjectKey):
        normalize_object_key("a/./b")


def test_minio_configured_requires_endpoint_and_keys(settings: Settings) -> None:
    assert minio_configured(settings) is False
    settings.minio_endpoint = "127.0.0.1:9000"
    settings.minio_access_key = "ecochronos"
    settings.minio_secret_key = "ecochronos_local_dev_minio"
    assert minio_configured(settings) is True


def test_build_minio_client_requires_config(settings: Settings) -> None:
    with pytest.raises(ArchiveNotConfigured):
        build_minio_client(settings)


def test_archive_store_from_settings_is_none_when_unconfigured(settings: Settings) -> None:
    assert archive_store_from_settings(settings) is None


def test_from_settings_builds_store(settings: Settings) -> None:
    settings.minio_endpoint = "127.0.0.1:9000"
    settings.minio_access_key = "ecochronos"
    settings.minio_secret_key = "secret"
    settings.minio_bucket = "vault-archive"
    store = ArchiveStore.from_settings(settings)
    assert store.bucket == "vault-archive"


def test_put_bytes_ensures_bucket_and_uploads() -> None:
    client = MagicMock()
    client.bucket_exists.return_value = False
    client.put_object.return_value = MagicMock(etag="abc123")
    store = ArchiveStore(client, "ecochronos")

    meta = store.put_bytes("demo/hello.txt", b"hello", content_type="text/plain")

    client.make_bucket.assert_called_once_with("ecochronos")
    client.put_object.assert_called_once()
    args, kwargs = client.put_object.call_args
    assert args[0] == "ecochronos"
    assert args[1] == "demo/hello.txt"
    assert args[3] == 5
    assert kwargs["content_type"] == "text/plain"
    assert meta.size == 5
    assert meta.etag == "abc123"
    assert meta.key == "demo/hello.txt"


def test_put_stream_skips_make_bucket_when_present() -> None:
    client = MagicMock()
    client.bucket_exists.return_value = True
    client.put_object.return_value = MagicMock(etag="etag")
    store = ArchiveStore(client, "ecochronos")
    store.put_stream("a.bin", BytesIO(b"xy"), 2)
    client.make_bucket.assert_not_called()


def test_stat_maps_missing_object() -> None:
    client = MagicMock()
    client.stat_object.side_effect = _s3_error("NoSuchKey")
    store = ArchiveStore(client, "ecochronos")
    with pytest.raises(ObjectNotFound) as exc:
        store.stat("missing.bin")
    assert exc.value.key == "missing.bin"


def test_stat_returns_object_meta() -> None:
    client = MagicMock()
    client.stat_object.return_value = MagicMock(
        size=12,
        etag="deadbeef",
        content_type="application/octet-stream",
        last_modified=None,
    )
    store = ArchiveStore(client, "ecochronos")
    meta = store.stat("demo/a.bin")
    assert meta.size == 12
    assert meta.etag == "deadbeef"
    client.stat_object.assert_called_once_with("ecochronos", "demo/a.bin")


def test_iter_object_streams_and_releases_connection() -> None:
    client = MagicMock()
    response = MagicMock()
    response.stream.return_value = iter([b"ab", b"cd"])
    client.get_object.return_value = response
    store = ArchiveStore(client, "ecochronos", chunk_size=2)

    chunks = list(store.iter_object("demo/a.bin", offset=4, length=4))

    assert chunks == [b"ab", b"cd"]
    client.get_object.assert_called_once_with("ecochronos", "demo/a.bin", offset=4, length=4)
    response.close.assert_called_once()
    response.release_conn.assert_called_once()


def test_iter_object_maps_missing_and_still_closes() -> None:
    client = MagicMock()
    client.get_object.side_effect = _s3_error("NoSuchKey")
    store = ArchiveStore(client, "ecochronos")
    with pytest.raises(ObjectNotFound):
        list(store.iter_object("gone.bin"))


def test_iter_object_zero_length_does_not_touch_minio() -> None:
    client = MagicMock()
    store = ArchiveStore(client, "ecochronos")
    assert list(store.iter_object("demo/a.bin", offset=0, length=0)) == []
    client.get_object.assert_not_called()
