"""Grid + salary + attribution endpoints (S3).

The grid router exposes:

* ``POST /months/{id}/grid/compute`` — recompute the grid snapshot for the
  month. Admin-only because grid outputs feed close/audit and a TL should
  never silently overwrite a snapshot.
* ``GET /months/{id}/grid`` — read the latest grid snapshot rows.
* ``POST /months/{id}/salary`` — upsert an HR/payroll window (admin-only).
* ``GET /months/{id}/salary`` — read the salary windows for the tenant.
* ``GET /months/{id}/attribution`` — read the attributed projection rows.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..api.schemas import (
    AttributionMonthOut,
    AttributionRowOut,
    GridCalculationOut,
    GridComputeOut,
    SalaryMasterOut,
    SalaryUpsertIn,
)
from ..domain.enums import WorkingKind
from ..domain.rule_pack import RULE_PACK_VERSION
from ..repositories.models import (
    GridCalculation,
    Month,
    Person,
)
from ..repositories.months import MonthRepository
from ..repositories.salary import SalaryRepository
from ..services.attribution import AttributionService
from ..services.auth import Principal, assert_admin, assert_same_tenant
from ..services.grid import GridService

router = APIRouter(prefix="/months", tags=["grid"])


def _month_or_404(session: Session, month_id: str, principal: Principal) -> Month:
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    return month


@router.post("/{month_id}/grid/compute", response_model=GridComputeOut)
def compute_grid(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> GridComputeOut:
    assert_admin(principal)
    month = _month_or_404(session, month_id, principal)
    snapshots, rows = GridService(session).compute_and_persist(
        tenant_id=principal.tenant_id, month=month
    )
    return GridComputeOut(
        month_id=month.id,
        revision=month.revision,
        rule_pack_version=RULE_PACK_VERSION,
        snapshots=[
            GridCalculationOut.model_validate(row) for row in rows
        ],
    )


@router.get("/{month_id}/grid", response_model=list[GridCalculationOut])
def get_grid(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[GridCalculationOut]:
    month = _month_or_404(session, month_id, principal)
    rows = list(
        session.execute(
            select(GridCalculation).where(
                GridCalculation.tenant_id == principal.tenant_id,
                GridCalculation.month_id == month.id,
            ).order_by(GridCalculation.person_id)
        ).scalars()
    )
    return [GridCalculationOut.model_validate(row) for row in rows]


@router.post("/{month_id}/salary", response_model=SalaryMasterOut)
def upsert_salary(
    month_id: str,
    payload: SalaryUpsertIn,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> SalaryMasterOut:
    """Admin-only HR/payroll salary window upsert (S3 slice)."""

    assert_admin(principal)
    month = _month_or_404(session, month_id, principal)
    _ = month  # month id used only for scope; the salary row is tenant-wide
    person = session.get(Person, payload.person_id)
    if person is None or person.tenant_id != principal.tenant_id:
        from ..domain.errors import NotFoundError

        raise NotFoundError("person not found", details={"person_id": payload.person_id})
    row = SalaryRepository(session).upsert_window(
        tenant_id=principal.tenant_id,
        person_id=payload.person_id,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        salary=Decimal(payload.salary),
        tickets=Decimal(payload.tickets),
        flip=Decimal(payload.flip),
        source=payload.source,
        notes=payload.notes,
    )
    return SalaryMasterOut.model_validate(row)


@router.get("/{month_id}/salary", response_model=list[SalaryMasterOut])
def list_salary(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[SalaryMasterOut]:
    month = _month_or_404(session, month_id, principal)
    _ = month
    rows = SalaryRepository(session).list_for_tenant(principal.tenant_id)
    return [SalaryMasterOut.model_validate(row) for row in rows]


@router.get("/{month_id}/attribution", response_model=AttributionMonthOut)
def get_attribution(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> AttributionMonthOut:
    month = _month_or_404(session, month_id, principal)
    summary = AttributionService(session).summary_for_revision(
        tenant_id=principal.tenant_id,
        month=month,
        revision=month.revision,
    )
    rows = [
        AttributionRowOut(
            person_id=row.person_id,
            store_id=row.store_id,
            business_date=row.business_date,
            amount=row.amount,
            currency=row.currency,
            generation=row.generation,
            working_kind=WorkingKind(row.working_kind),
            revision=row.revision,
        )
        for row in summary.attributed
    ]
    return AttributionMonthOut(
        month_id=month.id,
        revision=summary.revision,
        total_rows=summary.total_rows,
        company_total=summary.company_total,
        rows=rows,
        anomalies=list(summary.anomalies),
    )


__all__ = ["router"]
