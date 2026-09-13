import pytest
from fastapi.testclient import TestClient

from ecochronos_vault.app import create_app
from ecochronos_vault.config import Settings


def test_create_app_returns_fastapi(settings: Settings) -> None:
    app = create_app(settings)
    assert app.title == "EcoChronos Vault"
    assert app.state.settings is settings


def test_healthz_is_ok_without_dependencies(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["dependencies"] == {"postgres": "skipped", "minio": "skipped"}


def test_healthz_stays_200_when_dependencies_error(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(
        "ecochronos_vault.app.collect_dependency_status",
        lambda _settings: {"postgres": "error", "minio": "error"},
    )
    response = TestClient(create_app(settings)).get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["dependencies"]["postgres"] == "error"


def test_readyz_not_ready_when_dependencies_skipped(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["dependencies"]["postgres"] == "skipped"
    assert body["dependencies"]["minio"] == "skipped"


def test_readyz_ok_when_dependencies_connected(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(
        "ecochronos_vault.app.collect_dependency_status",
        lambda _settings: {"postgres": "connected", "minio": "connected"},
    )
    response = TestClient(create_app(settings)).get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
