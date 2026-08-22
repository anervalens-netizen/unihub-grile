"""Grid, payroll-master, attribution and holiday endpoints."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
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
from ..domain.enums import MonthState, WorkingKind
from ..domain.errors import ConflictError, NotFoundError
from ..domain.rule_pack import RULE_PACK_VERSION
from ..repositories.holidays import HolidayMarker, HolidayRepository
from ..repositories.models import GridCalculation, Month, Person
from ..repositories.months import MonthRepository
from ..repositories.salary import SalaryRepository
from ..services.attribution import AttributionService
from ..services.auth import Principal, assert_same_tenant
from ..services.authorization import (
    Capability,
    authorize,
    authorize_store_for_month,
    month_store_ids,
)
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
    """Recompute the authoritative current-revision payroll grid."""

    authorize(principal, Capability.GRID_COMPUTE)
    month = _month_or_404(session, month_id, principal)
    _, rows = GridService(session).compute_and_persist(
        tenant_id=principal.tenant_id,
        month=month,
    )
    return GridComputeOut(
        month_id=month.id,
        revision=month.revision,
        rule_pack_version=RULE_PACK_VERSION,
        snapshots=[GridCalculationOut.model_validate(row) for row in rows],
    )


@router.get("/{month_id}/grid", response_model=list[GridCalculationOut])
def get_grid(
    month_id: str,
    store_id: str | None = None,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[GridCalculationOut]:
    """Read the latest available current-rule-pack grid snapshot in caller scope."""

    authorize(principal, Capability.GRID_READ)
    month = _month_or_404(session, month_id, principal)
    allowed_stores = month_store_ids(session, principal, month)
    if store_id is not None:
        authorize_store_for_month(
            session,
            principal,
            Capability.GRID_READ,
            month=month,
            store_id=store_id,
        )
        allowed_stores = {store_id}
    if not allowed_stores:
        return []

    latest_revision = session.execute(
        select(func.max(GridCalculation.revision)).where(
            GridCalculation.tenant_id == principal.tenant_id,
            GridCalculation.month_id == month.id,
            GridCalculation.rule_pack_version == RULE_PACK_VERSION,
        )
    ).scalar_one()
    if latest_revision is None:
        return []

    rows = list(
        session.execute(
            select(GridCalculation)
            .where(
                GridCalculation.tenant_id == principal.tenant_id,
                GridCalculation.month_id == month.id,
                GridCalculation.revision == latest_revision,
                GridCalculation.rule_pack_version == RULE_PACK_VERSION,
                GridCalculation.store_id.in_(allowed_stores),
            )
            .order_by(GridCalculation.person_id)
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
    """Write payroll master data; closed periods require reopen first."""

    authorize(principal, Capability.PAYROLL_MASTER_WRITE)
    month = _month_or_404(session, month_id, principal)
    if month.state == MonthState.CLOSED.value:
        raise ConflictError(
            "month is closed",
            details={"code": "MONTH_CLOSED", "month_id": month.id},
        )
    person = session.get(Person, payload.person_id)
    if person is None or person.tenant_id != principal.tenant_id:
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
    """Read payroll master data; current policy is administrator-only."""

    authorize(principal, Capability.PAYROLL_MASTER_READ)
    _month_or_404(session, month_id, principal)
    rows = SalaryRepository(session).list_for_tenant(principal.tenant_id)
    return [SalaryMasterOut.model_validate(row) for row in rows]


@router.get("/{month_id}/attribution", response_model=AttributionMonthOut)
def get_attribution(
    month_id: str,
    store_id: str | None = None,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> AttributionMonthOut:
    """Return attribution rows, totals and anomalies only inside caller scope."""

    authorize(principal, Capability.GRID_READ)
    month = _month_or_404(session, month_id, principal)
    allowed = month_store_ids(session, principal, month)
    if store_id is not None:
        authorize_store_for_month(
            session,
            principal,
            Capability.GRID_READ,
            month=month,
            store_id=store_id,
        )
        allowed = {store_id}

    attribution_service = AttributionService(session)
    latest_rows = attribution_service.latest_attribution(
        tenant_id=principal.tenant_id,
        month=month,
    )
    attribution_revision = latest_rows[0].revision if latest_rows else month.revision
    summary = attribution_service.summary_for_revision(
        tenant_id=principal.tenant_id,
        month=month,
        revision=attribution_revision,
    )
    visible_source_rows = [row for row in summary.attributed if row.store_id in allowed]
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
        for row in visible_source_rows
    ]
    visible_total = sum((row.amount for row in visible_source_rows), Decimal("0"))

    anomalies: list[dict[str, object]] = []
    for anomaly in summary.anomalies:
        anomaly_store = anomaly.get("store_id")
        if anomaly_store is None:
            if principal.role.value == "ADMIN" and store_id is None:
                anomalies.append(dict(anomaly))
            continue
        if str(anomaly_store) in allowed:
            anomalies.append(dict(anomaly))

    return AttributionMonthOut(
        month_id=month.id,
        revision=summary.revision,
        total_rows=len(rows),
        company_total=visible_total,
        rows=rows,
        anomalies=anomalies,
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
    """Read informational versioned holiday markers for the month."""

    authorize(principal, Capability.HOLIDAY_READ)
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
    markers = HolidayRepository(session).markers_for_month(
        tenant_id=tenant_id,
        year=year,
        month=month,
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
    authorize(principal, Capability.HOLIDAY_WRITE)
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
    authorize(principal, Capability.HOLIDAY_WRITE)
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
