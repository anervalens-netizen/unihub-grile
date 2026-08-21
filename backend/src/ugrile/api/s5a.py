"""S5a API endpoints — E-pay fresh readback + Google projection structural view.

The router is admin-only for the readback (the only bounded edit per
AC-13) and the Google projection view; the projection enqueue path
delegates to the worker so the API never executes the adapter.

Endpoints:

* ``POST /months/{id}/epay/readback`` — record one fresh E-pay readback
  for a store. Valid 0..10 integers per category persist as
  ``is_valid=True``; everything else (blank, text, fraction, negative,
  >10) is audited as ``is_valid=False`` with the original raw_value
  preserved.
* ``GET /months/{id}/epay/freshness`` — read the freshness report for
  a store. ``is_fresh=False`` when there is at least one working agent
  whose pair lacks a recent valid observation.
* ``GET /months/{id}/sheet-projection`` — return the last-good
  projection for a store, with the structural ``grila``/``pontaj``
  payloads the S5a fake adapter persists.
* ``POST /months/{id}/sheet-projection/enqueue`` — enqueue a
  ``GOOGLE_PROJECTION_STORE`` job for the worker. Admin-only.

The endpoints never touch a Google Sheet; the fake adapter is the sole
projection writer during S5a.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..api.schemas import (
    EpayFreshnessOut,
    EpayReadbackIn,
    EpayReadbackItemOut,
    EpayReadbackOut,
    JobOut,
    SheetProjectionEnqueueIn,
    SheetProjectionOut,
    SheetProjectionPayloadOut,
)
from ..connectors.google import (
    GoogleAdapterError,
    read_store_projection,
)
from ..connectors.google import (
    last_error as google_last_error,
)
from ..connectors.google import (
    last_run_at as google_last_run_at,
)
from ..domain.enums import JobKind
from ..domain.errors import DomainError
from ..repositories.models import Month, SheetProjectionRun
from ..repositories.months import MonthRepository
from ..services.auth import (
    Principal,
    assert_admin,
    assert_same_tenant,
)
from ..services.epay import freshness_for_month, record_readback
from ..worker.worker import enqueue

router = APIRouter(prefix="/months", tags=["s5a-epay-sheets"])


def _month_for(session: Session, month_id: str, principal: Principal) -> Month:
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    return month


@router.post("/{month_id}/epay/readback", response_model=EpayReadbackOut)
def post_epay_readback(
    month_id: str,
    payload: EpayReadbackIn,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> EpayReadbackOut:
    """Admin-only E-pay readback for one store/month.

    The caller submits one entry per agent/category (max two categories,
    one per working agent); the service persists all rows in a single
    transaction and returns the validated item list so the manager UI
    can render a precise audit summary.
    """

    assert_admin(principal)
    month = _month_for(session, month_id, principal)
    try:
        result = record_readback(
            session,
            tenant_id=principal.tenant_id,
            month_id=month.id,
            store_id=payload.store_id,
            observations=[entry.model_dump() for entry in payload.observations],
            actor_id=principal.user_id,
        )
    except DomainError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    session.commit()
    return EpayReadbackOut(
        store_id=result.store_id,
        month_id=result.month_id,
        observed_at=result.observed_at.isoformat(),
        valid_count=result.valid_count,
        invalid_count=result.invalid_count,
        items=[
            EpayReadbackItemOut(
                person_id=item.person_id,
                category=item.category,
                value=item.value,
                raw_value=item.raw_value,
                is_valid=item.is_valid,
            )
            for item in result.items
        ],
    )


@router.get("/{month_id}/epay/freshness", response_model=EpayFreshnessOut)
def get_epay_freshness(
    month_id: str,
    store_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> EpayFreshnessOut:
    """Read the freshness report for the (store, month) pair."""

    month = _month_for(session, month_id, principal)
    report = freshness_for_month(
        session,
        tenant_id=principal.tenant_id,
        store_id=store_id,
        month_id=month.id,
    )
    return EpayFreshnessOut(
        store_id=store_id,
        is_fresh=report.is_fresh,
        fresh_count=report.fresh_count,
        expected_count=report.expected_count,
        threshold=report.threshold.isoformat(),
    )


@router.get("/{month_id}/sheet-projection", response_model=SheetProjectionOut)
def get_sheet_projection(
    month_id: str,
    store_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> SheetProjectionOut:
    """Read the last-good projection persisted by the fake adapter."""

    _month_for(session, month_id, principal)
    try:
        projection = read_store_projection(
            session,
            tenant_id=principal.tenant_id,
            store_id=store_id,
        )
    except GoogleAdapterError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    last_run = google_last_run_at(session, tenant_id=principal.tenant_id, store_id=store_id)
    last_error = google_last_error(session, tenant_id=principal.tenant_id, store_id=store_id)
    failures_value = 0
    if projection is not None:
        run = (
            session.query(SheetProjectionRun)
            .filter_by(
                tenant_id=principal.tenant_id,
                store_id=store_id,
                generation=projection.generation,
            )
            .order_by(SheetProjectionRun.id.desc())
            .first()
        )
        if run is not None:
            failures_value = int(run.failures or 0)
    payload: SheetProjectionPayloadOut | None = None
    if projection is not None:
        payload = SheetProjectionPayloadOut(
            grila=dict(projection.grila),
            pontaj=dict(projection.pontaj),
        )
    last_success = projection.generation if projection is not None else None
    return SheetProjectionOut(
        store_id=store_id,
        generation=projection.generation if projection is not None else "",
        last_success_generation=last_success,
        last_run_at=last_run.isoformat() if last_run else None,
        last_error=last_error,
        failures=failures_value,
        payload=payload,
    )


@router.post("/{month_id}/sheet-projection/enqueue", response_model=JobOut)
def post_sheet_projection_enqueue(
    month_id: str,
    payload: SheetProjectionEnqueueIn,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> JobOut:
    """Enqueue a ``GOOGLE_PROJECTION_STORE`` job for the worker."""

    assert_admin(principal)
    month = _month_for(session, month_id, principal)
    idempotency_key = payload.idempotency_key or (
        f"{JobKind.GOOGLE_PROJECTION_STORE.value}:{month.id}:{payload.store_id}"
    )
    row = enqueue(
        session,
        tenant_id=principal.tenant_id,
        kind=JobKind.GOOGLE_PROJECTION_STORE.value,
        idempotency_key=idempotency_key,
        payload={
            "month_id": month.id,
            "store_id": payload.store_id,
            "year": month.year,
            "month": month.month,
            "revision": month.revision,
            "enqueued_by": principal.user_id,
        },
    )
    session.commit()
    return JobOut.model_validate(row)


__all__ = ["router"]
