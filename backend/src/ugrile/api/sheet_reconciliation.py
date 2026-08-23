"""Operational readback metadata for the last verified store Sheet projection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..connectors.google import read_store_projection
from ..repositories.months import MonthRepository
from ..services.auth import Principal, assert_same_tenant
from ..services.authorization import Capability, authorize_store_for_month
from .deps import current_principal, db_session

router = APIRouter(prefix="/months", tags=["google-sheets"])


class SheetReconciliationOut(BaseModel):
    store_id: str
    month_id: str
    available: bool
    generation: str | None = None
    format_version: str | None = None
    revision: int | None = None
    rule_pack_version: str | None = None
    projected_at: str | None = None
    verification_mode: str | None = None
    verified: bool = False
    grila_rows: int | None = None
    pontaj_rows: int | None = None
    grila_checksum_sha256: str | None = None
    pontaj_checksum_sha256: str | None = None
    projection_checksum_sha256: str | None = None


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _integer(value: Any) -> int | None:
    if type(value) is int:
        return value
    return None


def _out(
    *,
    store_id: str,
    month_id: str,
    generation: str,
    reconciliation: Mapping[str, Any],
) -> SheetReconciliationOut:
    return SheetReconciliationOut(
        store_id=store_id,
        month_id=month_id,
        available=True,
        generation=generation,
        format_version=_text(reconciliation.get("format_version")),
        revision=_integer(reconciliation.get("revision")),
        rule_pack_version=_text(reconciliation.get("rule_pack_version")),
        projected_at=_text(reconciliation.get("projected_at")),
        verification_mode=_text(reconciliation.get("verification_mode")),
        verified=reconciliation.get("verified") is True,
        grila_rows=_integer(reconciliation.get("grila_rows")),
        pontaj_rows=_integer(reconciliation.get("pontaj_rows")),
        grila_checksum_sha256=_text(reconciliation.get("grila_checksum_sha256")),
        pontaj_checksum_sha256=_text(reconciliation.get("pontaj_checksum_sha256")),
        projection_checksum_sha256=_text(
            reconciliation.get("projection_checksum_sha256")
        ),
    )


@router.get("/{month_id}/sheet-reconciliation", response_model=SheetReconciliationOut)
def get_sheet_reconciliation(
    month_id: str,
    store_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> SheetReconciliationOut:
    """Return only reconciliation evidence proven for the requested month/store."""

    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    authorize_store_for_month(
        session,
        principal,
        Capability.SHEET_READ,
        month=month,
        store_id=store_id,
    )
    projection = read_store_projection(
        session,
        tenant_id=principal.tenant_id,
        store_id=store_id,
        month_id=month.id,
    )
    if projection is None or not projection.reconciliation:
        return SheetReconciliationOut(
            store_id=store_id,
            month_id=month.id,
            available=False,
        )
    return _out(
        store_id=store_id,
        month_id=month.id,
        generation=projection.generation,
        reconciliation=projection.reconciliation,
    )


__all__ = ["router"]
