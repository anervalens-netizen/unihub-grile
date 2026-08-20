"""Calendar write/read endpoints.

S1 exposes the atomic single-assignment seam; S2 adds the revisioned whole
calendar apply endpoint that keeps calendar, coverage and Pontaj derived from
one authority.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
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
from ..domain.errors import CoverageInvariantError, ScopeError, ValidationError
from ..repositories.assignments import AssignmentRepository
from ..repositories.models import Month, Person, SiteDayAssignment
from ..repositories.months import MonthRepository
from ..services.auth import (
    Principal,
    assert_manager_or_admin,
    assert_same_tenant,
    effective_store_ids,
)
from ..services.calendar import CalendarChange, CalendarService

router = APIRouter(prefix="/months", tags=["calendar"])


def _rehydrate(row: SiteDayAssignment) -> AssignmentOut:
    return AssignmentOut.model_validate(row)


def _allowed_by_date(session: Session, principal: Principal, month: Month) -> dict[date, set[str]]:
    year = month.year
    month_number = month.month
    return {
        date(year, month_number, day): effective_store_ids(
            session, principal, date(year, month_number, day)
        )
        for day in range(1, monthrange(year, month_number)[1] + 1)
    }


def _visible_rows(
    session: Session,
    rows: list[SiteDayAssignment],
    allowed_by_date: dict[date, set[str]],
) -> list[SiteDayAssignment]:
    person_ids = {row.person_id for row in rows}
    home_stores = {
        person.id: person.home_store_id
        for person in session.execute(select(Person).where(Person.id.in_(person_ids))).scalars()
    }
    return [
        row
        for row in rows
        if row.business_date in allowed_by_date
        and row.store_id in allowed_by_date[row.business_date]
        and home_stores.get(row.person_id) in allowed_by_date[row.business_date]
    ]


@router.get("", response_model=list[MonthOut])
def list_months(
    tenant_id: str | None = None,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[MonthOut]:
    if tenant_id is not None:
        assert_same_tenant(principal, tenant_id)
    else:
        tenant_id = principal.tenant_id
    return [MonthOut.model_validate(m) for m in MonthRepository(session).list_for_tenant(tenant_id)]


@router.post("/{month_id}/assignments", response_model=AssignmentOut)
def upsert_assignment(
    month_id: str,
    payload: AssignmentCreate,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> AssignmentOut:
    """Compatibility endpoint backed by the S2 calendar authority."""

    assert_manager_or_admin(principal)
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
                    session, principal, payload.business_date
                )
            },
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
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    allowed_by_date = _allowed_by_date(session, principal, month)
    if store_id is not None and not any(store_id in stores for stores in allowed_by_date.values()):
        raise ScopeError("store is outside manager scope", details={"store_id": store_id})
    rows = AssignmentRepository(session).list_for_month(month_id, store_id=store_id)
    return [_rehydrate(r) for r in _visible_rows(session, rows, allowed_by_date)]


@router.get("/{month_id}/coverage", response_model=CoverageReport)
def month_coverage(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> CoverageReport:
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    allowed_by_date = _allowed_by_date(session, principal, month)
    rows = AssignmentRepository(session).list_for_month(month_id)
    projections = AssignmentRepository(session).project_assignments(
        _visible_rows(session, rows, allowed_by_date)
    )
    from ..domain.calendar import check_coverage

    conflicts = check_coverage(projections)
    return CoverageReport(
        month_id=month.id,
        conflicts=[
            ConflictOut(
                code=c.code,
                message=c.message,
                store_id=c.store_id,
                person_id=c.person_id,
                business_date=c.business_date.isoformat() if c.business_date else None,
            )
            for c in conflicts
        ],
    )


@router.post("/{month_id}/calendar/apply", response_model=CalendarProjectionOut)
def apply_calendar(
    month_id: str,
    payload: CalendarApplyIn,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> CalendarProjectionOut:
    assert_manager_or_admin(principal)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    changes = [
        CalendarChange(
            person_id=c.person_id,
            business_date=c.business_date,
            store_id=c.store_id,
            status=c.status,
            working_kind=c.working_kind,
        )
        for c in payload.changes
    ]
    allowed_by_date = {
        date(month.year, month.month, day): effective_store_ids(
            session, principal, date(month.year, month.month, day)
        )
        for day in range(1, monthrange(month.year, month.month)[1] + 1)
    }
    result = CalendarService(session).apply(
        month=month,
        tenant_id=principal.tenant_id,
        changes=changes,
        expected_revision=payload.expected_revision,
        allowed_store_ids_by_date=allowed_by_date,
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
