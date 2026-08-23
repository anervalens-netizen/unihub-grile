"""Administrative lifecycle endpoints for permanent store↔Sheet bindings."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain.errors import ConflictError
from ..repositories.models import SheetBinding
from ..services.auth import Principal
from ..services.authorization import Capability, authorize
from ..services.sheet_bindings import (
    configure_sheet_binding,
    get_sheet_binding,
    require_sheet_binding,
)
from .deps import current_principal, db_session
from .schemas import SheetBindingConfigureIn, SheetBindingOut

router = APIRouter(prefix="/sheet-bindings", tags=["google-sheets"])


class SheetBindingConfigureRequest(SheetBindingConfigureIn):
    """Binding request plus the complete last-seen identity for CAS rebinds."""

    expected_current_sheet_name_grila: str | None = Field(default=None, max_length=64)
    expected_current_sheet_name_pontaj: str | None = Field(default=None, max_length=64)


def _out(binding: SheetBinding) -> SheetBindingOut:
    return SheetBindingOut.model_validate(binding)


@router.get("/{store_id}", response_model=SheetBindingOut)
def get_binding(
    store_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> SheetBindingOut:
    authorize(principal, Capability.SHEET_BIND)
    binding = require_sheet_binding(
        session,
        tenant_id=principal.tenant_id,
        store_id=store_id,
    )
    return _out(binding)


@router.put("/{store_id}", response_model=SheetBindingOut)
def put_binding(
    store_id: str,
    payload: SheetBindingConfigureRequest,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> SheetBindingOut:
    """Create idempotently or explicitly rebind one store.

    Rebinding requires the complete last-seen Sheet identity plus a reason.
    Database uniqueness remains the final authority for concurrent attempts.
    """

    authorize(principal, Capability.SHEET_BIND)
    try:
        outcome = configure_sheet_binding(
            session,
            tenant_id=principal.tenant_id,
            store_id=store_id,
            spreadsheet_id=payload.spreadsheet_id,
            sheet_name_grila=payload.sheet_name_grila,
            sheet_name_pontaj=payload.sheet_name_pontaj,
            actor_id=principal.user_id,
            expected_current_spreadsheet_id=payload.expected_current_spreadsheet_id,
            expected_current_sheet_name_grila=payload.expected_current_sheet_name_grila,
            expected_current_sheet_name_pontaj=payload.expected_current_sheet_name_pontaj,
            reason=payload.reason,
        )
        session.commit()
        return _out(outcome.binding)
    except IntegrityError as exc:
        session.rollback()
        winner = get_sheet_binding(
            session,
            tenant_id=principal.tenant_id,
            store_id=store_id,
        )
        if (
            winner is not None
            and winner.spreadsheet_id == payload.spreadsheet_id
            and winner.sheet_name_grila == payload.sheet_name_grila.strip()
            and winner.sheet_name_pontaj == payload.sheet_name_pontaj.strip()
        ):
            return _out(winner)
        raise ConflictError(
            "sheet binding changed concurrently or spreadsheet is already assigned",
            details={"code": "SHEET_BINDING_CONFLICT"},
        ) from exc


__all__ = ["router"]
