"""Operational Google Sheet E-pay readback endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..connectors.google_provider import build_google_live_transport
from ..repositories.months import MonthRepository
from ..services.auth import Principal, assert_same_tenant
from ..services.authorization import Capability, authorize_store_for_month
from ..services.google_epay import read_epay_from_google_sheet
from .deps import current_principal, db_session
from .google_epay_models import GoogleEpayReadbackOut
from .schemas import EpayReadbackItemOut

router = APIRouter(prefix="/months", tags=["epay-sheets"])


@router.post("/{month_id}/epay/google-readback", response_model=GoogleEpayReadbackOut)
def post_google_epay_readback(
    month_id: str,
    store_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> GoogleEpayReadbackOut:
    """Read the exact protected E-pay input block from the current store Sheet."""

    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    authorize_store_for_month(
        session,
        principal,
        Capability.EPAY_WRITE,
        month=month,
        store_id=store_id,
    )
    transport = build_google_live_transport(require_mutations=False)
    result = read_epay_from_google_sheet(
        session,
        tenant_id=principal.tenant_id,
        month_id=month.id,
        store_id=store_id,
        actor_id=principal.user_id,
        reader=transport,
    )
    session.commit()
    readback = result.readback
    return GoogleEpayReadbackOut(
        store_id=readback.store_id,
        month_id=readback.month_id,
        observed_at=readback.observed_at.isoformat(),
        valid_count=readback.valid_count,
        invalid_count=readback.invalid_count,
        structure_valid=result.structure_valid,
        structural_errors=list(result.structural_errors),
        items=[
            EpayReadbackItemOut(
                person_id=item.person_id,
                category=item.category,
                value=item.value,
                raw_value=item.raw_value,
                is_valid=item.is_valid,
            )
            for item in readback.items
        ],
    )


__all__ = ["router"]
