"""Calendar/month compatibility read endpoints.

Business calendar writes are intentionally not exposed here. The canonical
mutation surfaces are the manager Program cell API and signed XLSX apply, both
of which converge on ``CalendarService.apply`` with revision/CAS and audit.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..api.schemas import AssignmentOut, ConflictOut, CoverageReport, MonthOut
from ..repositories.assignments import AssignmentRepository
from ..repositories.models import Month, SiteDayAssignment
from ..repositories.months import MonthRepository
from ..services.auth import Principal, assert_same_tenant, effective_store_ids
from ..services.authorization import Capability, authorize, authorize_store_for_month
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


__all__ = ["router"]
