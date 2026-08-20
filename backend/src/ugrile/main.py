"""FastAPI application entry point.

The app is intentionally small at S1 — health/readiness, catalog, months,
assignments, ingest, worker probe — to prove the seams without committing
to product surface area that S2 will define.
"""

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
        version="0.1.0",
        description=(
            "UniHub Grile standalone foundation. S1/S2/S3: stack, schema, calendar, "
            "derived Pontaj, XLSX schedule import, fixture connector, auth/scopes "
            "seam, one worker, sales attribution, rule-pack/grid engine, "
            "admin-only close/reopen core."
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
            "stage": "S3",
            "version": "0.1.0",
            "env": settings.app_env,
        }

    app.include_router(health_router)
    app.include_router(catalog_router)
    app.include_router(assignments_router)
    app.include_router(ingest_router)
    app.include_router(schedule_router)
    app.include_router(grid_router)
    app.include_router(close_router)
    app.include_router(s4_router)
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
