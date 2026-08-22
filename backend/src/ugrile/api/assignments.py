"""Calendar compatibility read/write endpoints."""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..api.schemas import (
    AssignmentCreate,
    AssignmentOut,
    CalendarApplyIn,
    CalendarProjectionOut,
    ConflictOut,
    CoverageReport,
    MonthOut,
)
from ..domain.enums import DayStatus
from ..domain.errors import CoverageInvariantError, ValidationError
from ..repositories.assignments import AssignmentRepository
from ..repositories.models import Month, SiteDayAssignment
from ..repositories.months import MonthRepository
from ..services.auth import Principal, assert_same_tenant, effective_store_ids
from ..services.authorization import Capability, authorize, authorize_store_for_month
from ..services.calendar import CalendarChange, CalendarService
from ..services.person_scope import effective_home_store_map

router = APIRouter(prefix="/months", tags=["calendar"])


def _rehydrate(row: SiteDayAssignment) -> AssignmentOut:
    return AssignmentOut.model_validate(row)


def _allowed_by_date(
    session: Session,
    principal: Principal,
    month: Month,
) -> dict[date, set[str]]:
    return {
        business_date: effective_store_ids(session, principal, business_date)
        for business_date in (
            date(month.year, month.month, day)
            for day in range(1, monthrange(month.year, month.month)[1] + 1)
        )
    }


def _visible_rows(
    session: Session,
    *,
    tenant_id: str,
    rows: list[SiteDayAssignment],
    allowed_by_date: dict[date, set[str]],
) -> list[SiteDayAssignment]:
    effective_home = effective_home_store_map(
        session,
        tenant_id=tenant_id,
        person_ids={row.person_id for row in rows},
        business_dates={row.business_date for row in rows},
    )
    return [
        row
        for row in rows
        if row.business_date in allowed_by_date
        and row.store_id in allowed_by_date[row.business_date]
        and effective_home.get((row.person_id, row.business_date))
        in allowed_by_date[row.business_date]
    ]


@router.get("", response_model=list[MonthOut])
def list_months(
    tenant_id: str | None = None,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[MonthOut]:
    authorize(principal, Capability.MONTH_READ)
    requested_tenant = tenant_id or principal.tenant_id
    assert_same_tenant(principal, requested_tenant)
    return [
        MonthOut.model_validate(month)
        for month in MonthRepository(session).list_for_tenant(requested_tenant)
    ]


@router.post("/{month_id}/assignments", response_model=AssignmentOut)
def upsert_assignment(
    month_id: str,
    payload: AssignmentCreate,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> AssignmentOut:
    """Compatibility endpoint backed by the revisioned calendar authority."""

    authorize(principal, Capability.SCHEDULE_WRITE)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    expected_revision = (
        payload.expected_revision if payload.expected_revision is not None else month.revision
    )
    try:
        result = CalendarService(session).apply(
            month=month,
            tenant_id=principal.tenant_id,
            changes=[
                CalendarChange(
                    person_id=payload.person_id,
                    business_date=payload.business_date,
                    store_id=payload.store_id,
                    status=DayStatus.WORKING,
                    working_kind=payload.working_kind,
                )
            ],
            expected_revision=expected_revision,
            allowed_store_ids_by_date={
                payload.business_date: effective_store_ids(
                    session,
                    principal,
                    payload.business_date,
                )
            },
            actor_id=principal.user_id,
            source="API_ASSIGNMENT",
        )
    except CoverageInvariantError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    for row in result.assignments:
        if (
            row.person_id == payload.person_id
            and row.store_id == payload.store_id
            and row.business_date == payload.business_date
        ):
            return _rehydrate(row)
    raise ValidationError("calendar apply did not return the requested assignment")


@router.get("/{month_id}/assignments", response_model=list[AssignmentOut])
def list_assignments(
    month_id: str,
    store_id: str | None = None,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[AssignmentOut]:
    authorize(principal, Capability.SCHEDULE_READ)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    if store_id is not None:
        authorize_store_for_month(
            session,
            principal,
            Capability.SCHEDULE_READ,
            month=month,
            store_id=store_id,
        )
    allowed_by_date = _allowed_by_date(session, principal, month)
    rows = AssignmentRepository(session).list_for_month(month_id, store_id=store_id)
    visible = _visible_rows(
        session,
        tenant_id=principal.tenant_id,
        rows=rows,
        allowed_by_date=allowed_by_date,
    )
    return [_rehydrate(row) for row in visible]


@router.get("/{month_id}/coverage", response_model=CoverageReport)
def month_coverage(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> CoverageReport:
    authorize(principal, Capability.SCHEDULE_READ)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    allowed_by_date = _allowed_by_date(session, principal, month)
    rows = AssignmentRepository(session).list_for_month(month_id)
    visible = _visible_rows(
        session,
        tenant_id=principal.tenant_id,
        rows=rows,
        allowed_by_date=allowed_by_date,
    )
    projections = AssignmentRepository(session).project_assignments(visible)
    from ..domain.calendar import check_coverage

    conflicts = check_coverage(projections)
    return CoverageReport(
        month_id=month.id,
        conflicts=[
            ConflictOut(
                code=conflict.code,
                message=conflict.message,
                store_id=conflict.store_id,
                person_id=conflict.person_id,
                business_date=(
                    conflict.business_date.isoformat() if conflict.business_date else None
                ),
            )
            for conflict in conflicts
        ],
    )


@router.post("/{month_id}/calendar/apply", response_model=CalendarProjectionOut)
def apply_calendar(
    month_id: str,
    payload: CalendarApplyIn,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> CalendarProjectionOut:
    authorize(principal, Capability.SCHEDULE_WRITE)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    changes = [
        CalendarChange(
            person_id=change.person_id,
            business_date=change.business_date,
            store_id=change.store_id,
            status=change.status,
            working_kind=change.working_kind,
        )
        for change in payload.changes
    ]
    result = CalendarService(session).apply(
        month=month,
        tenant_id=principal.tenant_id,
        changes=changes,
        expected_revision=payload.expected_revision,
        allowed_store_ids_by_date=_allowed_by_date(session, principal, month),
        actor_id=principal.user_id,
        source="API_CALENDAR",
    )
    return CalendarProjectionOut(
        month_id=result.month_id,
        revision=result.revision,
        assignment_count=len(result.assignments),
        person_calendar_count=len(result.person_calendar),
        coverage_count=len(result.coverage),
        pontaj_count=len(result.pontaj),
    )


__all__ = ["router"]
