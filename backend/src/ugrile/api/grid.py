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

from datetime import date
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
    HolidayCalendarUpsertIn,
    HolidayMarkerOut,
    HolidayMonthOut,
    HolidayOverrideIn,
    SalaryMasterOut,
    SalaryUpsertIn,
)
from ..domain.enums import WorkingKind
from ..domain.rule_pack import RULE_PACK_VERSION
from ..repositories.holidays import HolidayMarker, HolidayRepository
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


def _marker_out(marker: HolidayMarker) -> HolidayMarkerOut:
    return HolidayMarkerOut(
        version=marker.version,
        business_date=marker.business_date,
        label=marker.label,
        is_active=marker.is_active,
        override_active=marker.override_active,
        override_reason=marker.override_reason,
    )


@router.get("/{month_id}/holidays", response_model=HolidayMonthOut)
def get_holidays(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> HolidayMonthOut:
    """Read the versioned holiday markers (plus admin overrides) for the month.

    Read-only for any same-tenant principal; the markers are informational
    and never affect schedule, Pontaj, target or pay.
    """

    month = _month_or_404(session, month_id, principal)
    markers = HolidayRepository(session).markers_for_month(
        tenant_id=principal.tenant_id,
        year=month.year,
        month=month.month,
    )
    return HolidayMonthOut(
        month_id=month.id,
        markers=[_marker_out(marker) for marker in markers],
    )


def _holiday_marker_for(
    session: Session,
    *,
    tenant_id: str,
    year: int,
    month: int,
    version: str,
    business_date: date,
) -> HolidayMarkerOut:
    """Return the full marker for one (version, date) after an admin write."""

    markers = HolidayRepository(session).markers_for_month(
        tenant_id=tenant_id, year=year, month=month
    )
    for marker in markers:
        if marker.version == version and marker.business_date == business_date:
            return _marker_out(marker)
    raise RuntimeError(f"holiday marker not found after upsert: {version} {business_date}")


@router.post("/{month_id}/holidays", response_model=HolidayMarkerOut)
def upsert_holiday_calendar(
    month_id: str,
    payload: HolidayCalendarUpsertIn,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> HolidayMarkerOut:
    """Admin-only upsert of a versioned Romanian legal holiday date."""

    assert_admin(principal)
    month = _month_or_404(session, month_id, principal)
    HolidayRepository(session).upsert_calendar(
        tenant_id=principal.tenant_id,
        version=payload.version,
        business_date=payload.business_date,
        label=payload.label,
        is_active=payload.is_active,
    )
    session.flush()
    return _holiday_marker_for(
        session,
        tenant_id=principal.tenant_id,
        year=month.year,
        month=month.month,
        version=payload.version,
        business_date=payload.business_date,
    )


@router.post("/{month_id}/holidays/override", response_model=HolidayMarkerOut)
def upsert_holiday_override(
    month_id: str,
    payload: HolidayOverrideIn,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> HolidayMarkerOut:
    """Admin-only override of a holiday marker (audited with reason + actor)."""

    assert_admin(principal)
    month = _month_or_404(session, month_id, principal)
    HolidayRepository(session).upsert_override(
        tenant_id=principal.tenant_id,
        version=payload.version,
        business_date=payload.business_date,
        is_active=payload.is_active,
        reason=payload.reason,
        actor_id=principal.user_id,
    )
    session.flush()
    return _holiday_marker_for(
        session,
        tenant_id=principal.tenant_id,
        year=month.year,
        month=month.month,
        version=payload.version,
        business_date=payload.business_date,
    )


__all__ = ["router"]
