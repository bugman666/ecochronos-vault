from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ecochronos_vault.archive import ArchiveStore, archive_store_from_settings
from ecochronos_vault.archive_http import router as archive_router
from ecochronos_vault.config import Settings, get_settings
from ecochronos_vault.health import DependencyStatus, collect_dependency_status
from ecochronos_vault.ingest import build_ingest_status_view
from ecochronos_vault.ingest.scheduler import start_scheduler
from ecochronos_vault.metadata import MetadataStore, metadata_store_from_settings
from ecochronos_vault.metadata_http import router as metadata_router


def create_app(
    settings: Settings | None = None,
    archive_store: ArchiveStore | None = None,
    metadata_store: MetadataStore | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler = None
        if settings.ingest_schedule_enabled:
            scheduler = start_scheduler(settings, run_immediately=True)
        app.state.ingest_scheduler = scheduler
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.shutdown(wait=False)

    app = FastAPI(
        title="EcoChronos Vault",
        version="0.1.0",
        summary="Open archive for high-resolution microclimate and surface ecology time series.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.ingest_scheduler = None
    app.state.archive_store = (
        archive_store if archive_store is not None else archive_store_from_settings(settings)
    )
    app.state.metadata_store = (
        metadata_store if metadata_store is not None else metadata_store_from_settings(settings)
    )
    app.include_router(archive_router)
    app.include_router(metadata_router)

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

    @app.get("/ingest/status")
    def ingest_status() -> dict[str, object]:
        """Last ingest run plus scheduler configuration."""
        return build_ingest_status_view(settings)

    return app


app = create_app()
