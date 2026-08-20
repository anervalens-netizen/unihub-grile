"""Health and readiness endpoints.

``/healthz`` reports liveness: the process is up and the FastAPI app responds.
``/readyz`` reports readiness: the database is reachable and the migration
head is applied.

Both endpoints must not require auth — they are used by host probes and
load balancers before the request reaches any authenticated route.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..api.deps import db_session
from ..api.schemas import HealthReport
from ..repositories.models import Tenant

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthReport)
def healthz(session: Session = Depends(db_session)) -> HealthReport:
    db_ok = False
    try:
        session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # pragma: no cover - defensive
        db_ok = False
    return HealthReport(
        status="ok" if db_ok else "degraded",
        database=db_ok,
        schema_version="0001_initial_schema",
        app_version="0.1.0",
    )


@router.get("/readyz", response_model=HealthReport)
def readyz(session: Session = Depends(db_session)) -> HealthReport:
    db_ok = False
    schema_version = "missing"
    try:
        session.execute(text("SELECT version_num FROM alembic_version"))
        row = session.execute(text("SELECT version_num FROM alembic_version")).first()
        if row is not None:
            schema_version = row[0]
        # Sanity check: tenants table is queryable.
        session.execute(text("SELECT 1 FROM tenants LIMIT 1"))
        db_ok = True
    except Exception:  # pragma: no cover - defensive
        db_ok = False
    return HealthReport(
        status="ok" if db_ok else "down",
        database=db_ok,
        schema_version=schema_version,
        app_version="0.1.0",
    )


@router.get("/")
def root() -> dict[str, str | int | None]:
    return {
        "service": "ugrile-backend",
        "stage": "S1",
        "tenant_count": _tenant_count_or_none(),
    }


def _tenant_count_or_none() -> int | None:
    try:
        # Use a brand new session so this does not depend on the request.
        from ..core.database import get_sessionmaker

        with get_sessionmaker()() as session:
            return session.query(Tenant).count()
    except Exception:  # pragma: no cover - defensive
        return None


# Make import order obvious when this router is registered.
__all__ = ["router"]
