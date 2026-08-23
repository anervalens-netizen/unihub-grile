"""FastAPI application entry point."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from .api.assignments import router as assignments_router
from .api.catalog import router as catalog_router
from .api.close import router as close_router
from .api.error_contract import (
    domain_error_response,
    http_error_response,
    validation_error_response,
)
from .api.google_epay import router as google_epay_router
from .api.grid import router as grid_router
from .api.health import router as health_router
from .api.ingest import router as ingest_router
from .api.s4 import router as s4_router
from .api.s5a import router as s5a_router
from .api.s5b import router as s5b_router
from .api.schedule import router as schedule_router
from .api.session import router as session_router
from .api.sheet_bindings import router as sheet_bindings_router
from .api.sheet_reconciliation import router as sheet_reconciliation_router
from .api.worker_api import router as worker_router
from .core.config import get_settings
from .core.correlation import (
    CORRELATION_HEADER,
    bind_correlation_id,
    resolve_request_correlation_id,
)
from .core.logging import configure_logging, get_logger, safe_exception_fields
from .domain.errors import DomainError

configure_logging()
log = get_logger("ugrile.api")


def _route_template(request: Request) -> str:
    """Return the matched route template without logging concrete entity IDs."""

    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path else "unmatched"


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

    @app.middleware("http")
    async def _correlation_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        correlation_id = resolve_request_correlation_id(request.headers.get(CORRELATION_HEADER))
        started = perf_counter()
        with bind_correlation_id(correlation_id):
            try:
                response = await call_next(request)
            except Exception as exc:
                log.error(
                    "http_request_failed",
                    correlation_id=correlation_id,
                    method=request.method,
                    route=_route_template(request),
                    status_code=500,
                    duration_ms=round((perf_counter() - started) * 1000, 2),
                    **safe_exception_fields(exc),
                )
                raise
            response.headers[CORRELATION_HEADER] = correlation_id
            log.info(
                "http_request_completed",
                correlation_id=correlation_id,
                method=request.method,
                route=_route_template(request),
                status_code=response.status_code,
                duration_ms=round((perf_counter() - started) * 1000, 2),
            )
            return response

    @app.exception_handler(DomainError)
    async def _domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        return domain_error_response(exc)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return http_error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return validation_error_response(exc)

    @app.get("/version")
    def _version() -> dict[str, str]:
        return {
            "app": "ugrile-backend",
            "program": "standalone-plugin-candidate",
            "version": "0.2.0",
            "env": settings.app_env,
        }

    app.include_router(health_router)
    app.include_router(session_router)
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
    app.include_router(sheet_bindings_router)
    app.include_router(sheet_reconciliation_router)
    app.include_router(google_epay_router)
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
        access_log=False,
        reload=False,
    )


if __name__ == "__main__":
    main()
