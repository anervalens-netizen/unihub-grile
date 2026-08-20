"""Worker API surface (admin-only).

The worker is the **sole** authority that executes jobs (see
``ugrile.worker.worker``). The API layer is permitted to:

* **enqueue** typed jobs (``POST /worker/noop``, ``POST /worker/jobs``) so
  the manager UI can schedule work without holding the request open;
* **list** recent jobs (``GET /worker/jobs``) so operators can audit state.

The API MUST NOT execute jobs. The previous ``POST /worker/run`` and
``POST /ingest/fixture/run`` endpoints were removed because they violated
the single-authority contract; the worker started via
``python -m ugrile.worker.worker`` is the only path that calls
``run_once``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..api.schemas import JobEnqueueIn, JobOut
from ..domain.enums import JobKind
from ..repositories.models import OutboxJob
from ..services.auth import Principal, assert_admin
from ..worker.worker import enqueue

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


@router.post("/noop")
def enqueue_noop(
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> dict[str, object]:
    """Enqueue a NOOP job. The worker settles it asynchronously."""

    assert_admin(principal)
    import time

    row = enqueue(
        session,
        tenant_id=principal.tenant_id,
        kind=JobKind.NOOP.value,
        idempotency_key=f"noop:{principal.user_id}:{int(time.time())}",
    )
    session.commit()
    return {"enqueued": True, "job_id": row.id, "idempotency_key": row.idempotency_key}


@router.post("/jobs", response_model=JobOut)
def enqueue_job(
    payload: JobEnqueueIn,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> JobOut:
    """Enqueue a typed job. Idempotency key is required to dedupe retries.

    Only registered :class:`JobKind` values are accepted; ``NOOP`` is
    permitted through this endpoint so the manager UI can schedule a
    progress probe.
    """

    assert_admin(principal)
    try:
        kind = JobKind(payload.kind)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "UNKNOWN_JOB_KIND", "message": f"unknown job kind: {payload.kind}"},
        ) from exc
    idempotency_key = payload.idempotency_key or f"{kind.value}:{principal.user_id}"
    row = enqueue(
        session,
        tenant_id=principal.tenant_id,
        kind=kind.value,
        idempotency_key=idempotency_key,
        payload={"enqueued_by": principal.user_id},
    )
    session.commit()
    return JobOut.model_validate(row)


__all__ = ["router"]
