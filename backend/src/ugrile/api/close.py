"""Month close / reopen API.

Authorization is capability-based; transactional locking, validation and audit
remain service-owned. Final close uses the versioned financial close policy.
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
from ..services.authorization import Capability, authorize
from ..services.close import CloseRequest, ReopenRequest
from ..services.policy_close import PolicyCloseService

router = APIRouter(prefix="/months", tags=["close"])


@router.post("/{month_id}/close", response_model=CloseOutcomeOut)
def close_month(
    month_id: str,
    payload: CloseIn,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> CloseOutcomeOut:
    authorize(principal, Capability.MONTH_CLOSE)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    request = CloseRequest(
        actor_id=principal.user_id,
        role_value=principal.role.value,
        expected_revision=payload.expected_revision,
    )
    outcome = PolicyCloseService(session).close_month(
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
    authorize(principal, Capability.MONTH_REOPEN)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    request = ReopenRequest(
        actor_id=principal.user_id,
        role_value=principal.role.value,
        reason=payload.reason,
    )
    outcome = PolicyCloseService(session).reopen_month(
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


@router.get("/{month_id}/close-events", response_model=list[CloseEventOut])
def list_close_events(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[CloseEventOut]:
    authorize(principal, Capability.MONTH_READ)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    events = MonthCloseEventRepository(session).list_for_month(
        tenant_id=principal.tenant_id, month_id=month_id
    )
    return [CloseEventOut.model_validate(row) for row in events]


__all__ = ["router"]
