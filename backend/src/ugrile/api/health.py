"""Operational health, readiness and metrics endpoints.

Semantics are deliberately distinct:

* ``/livez`` proves only that the application process/event loop can answer;
* ``/healthz`` is a diagnostic dependency summary and always answers HTTP 200;
* ``/readyz`` is the traffic gate and returns HTTP 503 unless DB, schema and
  enabled-worker lease state are ready;
* ``/metrics`` exposes privacy-safe Prometheus text metrics.

None of these endpoints requires an application principal. They therefore must
never expose tenant/store/person/payroll/provider identifiers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from ..api.deps import db_session
from ..api.schemas import HealthReport
from ..core.config import get_settings
from ..core.metrics import render_metrics
from ..repositories.models import OutboxJob

APP_VERSION = "0.2.0"
router = APIRouter(tags=["health"])


@lru_cache(maxsize=1)
def _expected_schema_version() -> str:
    """Resolve the single Alembic head from the shipped migration scripts."""

    backend_root = Path(__file__).resolve().parents[3]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    return head or "missing"


def _dependency_report(session: Session) -> HealthReport:
    settings = get_settings()
    expected = _expected_schema_version()
    database = False
    schema_version: str | None = None
    stale_running_jobs: int | None = None

    try:
        session.execute(text("SELECT 1"))
        database = True
        row = session.execute(text("SELECT version_num FROM alembic_version")).first()
        if row is not None:
            schema_version = str(row[0])
        if settings.worker_enabled:
            cutoff = datetime.now(tz=UTC) - timedelta(seconds=settings.worker_lease_seconds)
            stale_running_jobs = int(
                session.scalar(
                    select(func.count())
                    .select_from(OutboxJob)
                    .where(
                        OutboxJob.status == "RUNNING",
                        or_(OutboxJob.locked_at.is_(None), OutboxJob.locked_at <= cutoff),
                    )
                )
                or 0
            )
        else:
            stale_running_jobs = 0
    except Exception:
        database = False

    schema_current = database and schema_version == expected
    worker_ready = not settings.worker_enabled or stale_running_jobs == 0
    ready = database and schema_current and worker_ready
    return HealthReport(
        status="ok" if ready else "down",
        database=database,
        schema_version=schema_version,
        expected_schema_version=expected,
        schema_current=schema_current,
        worker_enabled=settings.worker_enabled,
        stale_running_jobs=stale_running_jobs,
        app_version=APP_VERSION,
    )


@router.get("/livez")
def livez() -> dict[str, str]:
    """Process-only liveness; dependency failures must not trigger restart loops."""

    return {"status": "ok", "app_version": APP_VERSION}


@router.get("/healthz", response_model=HealthReport)
def healthz(session: Session = Depends(db_session)) -> HealthReport:
    """Human/operator dependency summary; always HTTP 200 when process is live."""

    report = _dependency_report(session)
    if report.status == "down":
        return report.model_copy(update={"status": "degraded"})
    return report


@router.get("/readyz", response_model=HealthReport)
def readyz(session: Session = Depends(db_session)) -> HealthReport | JSONResponse:
    """Traffic readiness gate: 503 unless every required dependency is current."""

    report = _dependency_report(session)
    if report.status == "ok":
        return report
    return JSONResponse(status_code=503, content=report.model_dump(mode="json"))


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(session: Session = Depends(db_session)) -> PlainTextResponse:
    """Prometheus text metrics with bounded labels and no business identifiers."""

    return PlainTextResponse(
        render_metrics(session),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@router.get("/")
def root() -> dict[str, str]:
    return {
        "service": "ugrile-backend",
        "program": "standalone-plugin-candidate",
        "version": APP_VERSION,
    }


__all__ = ["APP_VERSION", "router"]
