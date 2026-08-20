"""Month close / reopen API (AC-15 S3 slice).

Two admin-only endpoints:

* ``POST /months/{id}/close`` — runs the S3 blocker detection and
  transitions the month to ``CLOSED`` on success; appends an audit row.
* ``POST /months/{id}/reopen`` — bumps the revision and transitions to
  ``REOPENED``; requires a non-empty reason; appends an audit row.

The append-only chain is read through
``GET /months/{id}/close-events``.

Non-admin callers receive ``403 FORBIDDEN`` from the auth helpers; an
invalid close returns ``422 VALIDATION_ERROR`` with the precise blocker
list so the manager UI can render every blocking condition.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..api.schemas import (
    BlockerOut,
    CloseEventOut,
    CloseIn,
    CloseOutcomeOut,
    ReopenIn,
)
from ..domain.enums import MonthState
from ..repositories.close import MonthCloseEventRepository
from ..repositories.months import MonthRepository
from ..services.auth import Principal, assert_same_tenant
from ..services.close import CloseRequest, CloseService, ReopenRequest

router = APIRouter(prefix="/months", tags=["close"])


@router.post("/{month_id}/close", response_model=CloseOutcomeOut)
def close_month(
    month_id: str,
    payload: CloseIn,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> CloseOutcomeOut:
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    if payload.expected_revision is not None and payload.expected_revision != month.revision:
        from ..domain.errors import StaleRevisionError

        raise StaleRevisionError(
            "stale close revision",
            details={
                "code": "STALE_REVISION",
                "expected": payload.expected_revision,
                "current": month.revision,
            },
        )
    request = CloseRequest(actor_id=principal.user_id, role_value=principal.role.value)
    outcome = CloseService(session).close_month(
        tenant_id=principal.tenant_id,
        month_id=month_id,
        request=request,
    )
    blockers = [
        BlockerOut(
            code=b.code,
            store_id=b.store_id,
            person_id=b.person_id,
            business_date=b.business_date,
            message=b.message,
        )
        for b in outcome.validation.blockers
    ]
    return CloseOutcomeOut(
        month_id=outcome.month_id,
        revision=outcome.revision,
        new_state=MonthState(outcome.new_state),
        audit_event_id=outcome.audit_event_id,
        blockers=blockers,
    )


@router.post("/{month_id}/reopen", response_model=CloseOutcomeOut)
def reopen_month(
    month_id: str,
    payload: ReopenIn,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> CloseOutcomeOut:
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    request = ReopenRequest(
        actor_id=principal.user_id,
        role_value=principal.role.value,
        reason=payload.reason,
    )
    outcome = CloseService(session).reopen_month(
        tenant_id=principal.tenant_id,
        month_id=month_id,
        request=request,
    )
    return CloseOutcomeOut(
        month_id=outcome.month_id,
        revision=outcome.revision,
        new_state=MonthState(outcome.new_state),
        audit_event_id=outcome.audit_event_id,
        blockers=[],
    )


@router.get(
    "/{month_id}/close-events", response_model=list[CloseEventOut]
)
def list_close_events(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[CloseEventOut]:
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    events = MonthCloseEventRepository(session).list_for_month(
        tenant_id=principal.tenant_id, month_id=month_id
    )
    return [CloseEventOut.model_validate(row) for row in events]


__all__ = ["router"]
