"""FastAPI application entry point."""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api.assignments import router as assignments_router
from .api.catalog import router as catalog_router
from .api.close import router as close_router
from .api.grid import router as grid_router
from .api.health import router as health_router
from .api.ingest import router as ingest_router
from .api.s4 import router as s4_router
from .api.s5a import router as s5a_router
from .api.s5b import router as s5b_router
from .api.schedule import router as schedule_router
from .api.worker_api import router as worker_router
from .core.config import get_settings
from .core.logging import configure_logging, get_logger
from .domain.errors import DomainError

configure_logging()
log = get_logger("ugrile.api")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ugrile-backend",
        version="0.2.0",
        description=(
            "UniHub Grile standalone plugin candidate: calendar, derived Pontaj, "
            "sales attribution, versioned grid engine, Google/XLSX projections, "
            "audited close/reopen and explicit future Retail integration seams."
        ),
    )

    @app.exception_handler(DomainError)
    async def _domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        )

    @app.get("/version")
    def _version() -> dict[str, str]:
        return {
            "app": "ugrile-backend",
            "program": "standalone-plugin-candidate",
            "version": "0.2.0",
            "env": settings.app_env,
        }

    app.include_router(health_router)
    app.include_router(catalog_router)
    app.include_router(assignments_router)
    # Fixture ingestion is a development/test tool and must not even be
    # discoverable in a production OpenAPI surface.
    if settings.app_env != "prod":
        app.include_router(ingest_router)
    app.include_router(schedule_router)
    app.include_router(grid_router)
    app.include_router(close_router)
    app.include_router(s4_router)
    app.include_router(s5a_router)
    app.include_router(s5b_router)
    app.include_router(worker_router)
    return app


app = create_app()


def main() -> None:
    """Run uvicorn programmatically (used by `python -m ugrile.main`)."""

    import uvicorn

    port = int(os.environ.get("UGRILE_PORT", "8080"))
    host = os.environ.get("UGRILE_HOST", "127.0.0.1")
    uvicorn.run(
        "ugrile.main:app",
        host=host,
        port=port,
        log_level=get_settings().log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    main()
