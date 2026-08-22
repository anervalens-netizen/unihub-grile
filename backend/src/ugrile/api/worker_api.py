"""Worker API surface.

The durable worker remains the sole authority that executes jobs. HTTP routes
may enqueue work and inspect durable state, but they never call ``run_once``.

Operational diagnostics deliberately expose a redacted view of job state:
raw payloads and idempotency keys stay out of the manager-facing response.
Admins see jobs only for their own tenant. Managers see only jobs whose
persisted month/store resources are wholly inside their effective scope.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..api.schemas import JobEnqueueIn, JobOut
from ..domain.enums import JobKind, RoleName
from ..repositories.models import Month, OutboxJob
from ..services.auth import Principal, assert_admin
from ..services.authorization import Capability, authorize, month_store_ids
from ..worker.policy import retry_policy_for
from ..worker.worker import enqueue

router = APIRouter(prefix="/worker", tags=["worker"])

QueueState = Literal["QUEUED", "RETRY", "RUNNING", "FAILED", "DONE"]


class JobStateCountsOut(BaseModel):
    queued: int
    retrying: int
    running: int
    failed: int
    done: int


class JobDiagnosticOut(BaseModel):
    id: int
    kind: str
    state: QueueState
    attempts: int
    max_attempts: int
    run_after: datetime
    locked_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_error: str | None
    month_id: str | None
    store_ids: list[str]


class JobDiagnosticsOut(BaseModel):
    counts: JobStateCountsOut
    jobs: list[JobDiagnosticOut]
    terminal_history_limit: int


def _decoded_payload(row: OutboxJob) -> dict[str, object] | None:
    """Read persisted resource metadata without surfacing malformed payloads."""

    try:
        payload = json.loads(row.payload or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _resource_from_payload(row: OutboxJob) -> tuple[str | None, set[str]]:
    payload = _decoded_payload(row)
    if payload is None:
        return None, set()

    raw_month_id = payload.get("month_id")
    month_id = raw_month_id if isinstance(raw_month_id, str) and raw_month_id else None

    store_ids: set[str] = set()
    raw_store_id = payload.get("store_id")
    if isinstance(raw_store_id, str) and raw_store_id:
        store_ids.add(raw_store_id)
    raw_store_ids = payload.get("store_ids")
    if isinstance(raw_store_ids, list):
        store_ids.update(
            value for value in raw_store_ids if isinstance(value, str) and value
        )
    return month_id, store_ids


def _manager_can_read_job(
    session: Session,
    principal: Principal,
    row: OutboxJob,
    scope_cache: dict[str, set[str]],
) -> bool:
    """Fail closed unless a manager-visible resource can be proven."""

    month_id, store_ids = _resource_from_payload(row)
    if month_id is None or not store_ids:
        return False

    month = session.execute(
        select(Month).where(
            Month.id == month_id,
            Month.tenant_id == principal.tenant_id,
        )
    ).scalar_one_or_none()
    if month is None:
        return False

    allowed = scope_cache.get(month.id)
    if allowed is None:
        allowed = month_store_ids(session, principal, month)
        scope_cache[month.id] = allowed
    return store_ids.issubset(allowed)


def _queue_state(row: OutboxJob) -> QueueState:
    if row.status == "PENDING":
        return "RETRY" if row.attempts > 0 else "QUEUED"
    if row.status == "RUNNING":
        return "RUNNING"
    if row.status == "FAILED":
        return "FAILED"
    return "DONE"


def _to_diagnostic(row: OutboxJob) -> JobDiagnosticOut:
    month_id, store_ids = _resource_from_payload(row)
    return JobDiagnosticOut(
        id=row.id,
        kind=row.kind,
        state=_queue_state(row),
        attempts=row.attempts,
        max_attempts=retry_policy_for(row.kind).max_attempts,
        run_after=row.run_after,
        locked_at=row.locked_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_error=row.last_error,
        month_id=month_id,
        store_ids=sorted(store_ids),
    )


def _counts(rows: list[JobDiagnosticOut]) -> JobStateCountsOut:
    return JobStateCountsOut(
        queued=sum(row.state == "QUEUED" for row in rows),
        retrying=sum(row.state == "RETRY" for row in rows),
        running=sum(row.state == "RUNNING" for row in rows),
        failed=sum(row.state == "FAILED" for row in rows),
        done=sum(row.state == "DONE" for row in rows),
    )


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[JobOut]:
    """Legacy admin list, now strictly tenant-scoped."""

    assert_admin(principal)
    stmt = (
        select(OutboxJob)
        .where(OutboxJob.tenant_id == principal.tenant_id)
        .order_by(OutboxJob.id.desc())
        .limit(limit)
    )
    rows = list(session.execute(stmt).scalars())
    return [JobOut.model_validate(row) for row in rows]


@router.get("/jobs/diagnostics", response_model=JobDiagnosticsOut)
def job_diagnostics(
    terminal_limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> JobDiagnosticsOut:
    """Return complete active queue state plus bounded recent terminal history.

    All PENDING/RUNNING jobs for the tenant are considered so an old active job
    cannot disappear behind a history limit. DONE/FAILED history is bounded.
    Manager visibility is derived from persisted month/store resources and
    fails closed for unscoped or malformed jobs.
    """

    authorize(principal, Capability.JOBS_READ)
    active_rows = list(
        session.execute(
            select(OutboxJob)
            .where(
                OutboxJob.tenant_id == principal.tenant_id,
                OutboxJob.status.in_(("PENDING", "RUNNING")),
            )
            .order_by(OutboxJob.id.desc())
        ).scalars()
    )
    terminal_rows = list(
        session.execute(
            select(OutboxJob)
            .where(
                OutboxJob.tenant_id == principal.tenant_id,
                OutboxJob.status.in_(("DONE", "FAILED")),
            )
            .order_by(OutboxJob.id.desc())
            .limit(terminal_limit)
        ).scalars()
    )

    rows_by_id = {row.id: row for row in [*active_rows, *terminal_rows]}
    persisted = [rows_by_id[key] for key in sorted(rows_by_id, reverse=True)]

    if principal.role is not RoleName.ADMIN:
        scope_cache: dict[str, set[str]] = {}
        persisted = [
            row
            for row in persisted
            if _manager_can_read_job(session, principal, row, scope_cache)
        ]

    jobs = [_to_diagnostic(row) for row in persisted]
    return JobDiagnosticsOut(
        counts=_counts(jobs),
        jobs=jobs,
        terminal_history_limit=terminal_limit,
    )


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
