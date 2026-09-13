from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ecochronos_vault.archive import ArchiveStore, archive_store_from_settings
from ecochronos_vault.archive_http import router as archive_router
from ecochronos_vault.config import Settings, get_settings
from ecochronos_vault.health import DependencyStatus, collect_dependency_status


def create_app(
    settings: Settings | None = None,
    archive_store: ArchiveStore | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="EcoChronos Vault",
        version="0.1.0",
        summary="Open archive for high-resolution microclimate and surface ecology time series.",
    )
    app.state.settings = settings
    app.state.archive_store = (
        archive_store if archive_store is not None else archive_store_from_settings(settings)
    )
    app.include_router(archive_router)

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        """Liveness probe. Always 200 when the process is up."""
        return {
            "status": "ok",
            "dependencies": collect_dependency_status(settings),
        }

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        """Readiness probe. Requires Postgres and MinIO to be connected."""
        dependencies = collect_dependency_status(settings)
        ready = all(status == DependencyStatus.CONNECTED for status in dependencies.values())
        body = {
            "status": "ready" if ready else "not_ready",
            "dependencies": dependencies,
        }
        return JSONResponse(status_code=200 if ready else 503, content=body)

    return app


app = create_app()
