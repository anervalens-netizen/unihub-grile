"""Worker probe and job listing.

S1 only needs a health endpoint and a listing endpoint; richer lifecycle
controls land with the operations stage.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..api.schemas import JobOut
from ..domain.enums import JobKind
from ..repositories.models import OutboxJob
from ..services.auth import Principal, assert_admin
from ..worker.worker import run_once

router = APIRouter(prefix="/worker", tags=["worker"])


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(
    limit: int = 20,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[JobOut]:
    assert_admin(principal)
    stmt = select(OutboxJob).order_by(OutboxJob.id.desc()).limit(min(limit, 100))
    rows = list(session.execute(stmt).scalars())
    return [JobOut.model_validate(r) for r in rows]


@router.post("/run", response_model=JobOut | None)
def run_one(
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> JobOut | None:
    """Run one job iteration. Useful for manual recovery from the API."""

    assert_admin(principal)
    row, _result = run_once(locked_by="ugrile-api")
    if row is None:
        return None
    return JobOut.model_validate(row)


@router.post("/noop")
def enqueue_noop(
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> dict[str, bool]:
    assert_admin(principal)
    import time

    from ..worker.worker import enqueue as _enqueue

    _enqueue(
        session,
        tenant_id=principal.tenant_id,
        kind=JobKind.NOOP.value,
        idempotency_key=f"noop:{principal.user_id}:{int(time.time())}",
    )
    return {"enqueued": True}


__all__ = ["router"]
