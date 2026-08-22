"""Manager UI read/write endpoints.

These endpoints power the standalone plugin-candidate manager pages: Overview,
Program, Magazin, Agent, Excepții and Close. Resource visibility remains
manager-scope aware and every route declares an operation capability explicitly.
"""

from __future__ import annotations

import dataclasses
from calendar import monthrange
from collections.abc import Mapping
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..api.schemas import (
    CalendarCellEditIn,
    CalendarProjectionOut,
    ChecklistItemOut,
    CloseChecklistOut,
    ExceptionOut,
    OverviewKpiOut,
    OverviewManagerRowOut,
    OverviewNeedsAttentionOut,
    OverviewOut,
    ProgramCellOut,
    ProgramChoiceOut,
    ProgramChoicesOut,
    ProgramGridOut,
    ProgramRowOut,
    ReopenWithReasonIn,
)
from ..domain.enums import DayStatus, MonthState, WorkingKind
from ..domain.errors import DomainError, ScopeError
from ..repositories.models import Month, Person, PontajProjection
from ..repositories.months import MonthRepository
from ..services.auth import Principal, assert_same_tenant, effective_store_ids
from ..services.authorization import Capability, authorize
from ..services.calendar import CalendarChange, CalendarService
from ..services.close import ReopenRequest
from ..services.overview import ExceptionService, OverviewService, ProgramService
from ..services.person_scope import effective_home_store_map
from ..services.policy_checklist import PolicyCloseChecklistService
from ..services.policy_close import PolicyCloseService

router = APIRouter(prefix="/months", tags=["manager-ui"])


def _allowed_by_date(session: Session, principal: Principal, month: Month) -> dict[date, set[str]]:
    days = monthrange(month.year, month.month)[1]
    return {
        date(month.year, month.month, 1 + offset): effective_store_ids(
            session, principal, date(month.year, month.month, 1 + offset)
        )
        for offset in range(days)
    }


def _scope_or_admin(
    session: Session, principal: Principal, month: Month
) -> Mapping[date, set[str]] | None:
    if principal.role.value == "ADMIN":
        return None
    return _allowed_by_date(session, principal, month)


@router.get("/{month_id}/overview", response_model=OverviewOut)
def get_overview(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> OverviewOut:
    authorize(principal, Capability.SCHEDULE_READ)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    report = OverviewService(session).month_overview(
        tenant_id=principal.tenant_id,
        month=month,
        manager_scope_store_ids=_scope_or_admin(session, principal, month),
    )
    return OverviewOut(
        month_id=report.month_id,
        year=report.year,
        month=report.month,
        state=report.state,
        revision=report.revision,
        rule_pack_version=report.rule_pack_version,
        kpis=OverviewKpiOut(**dataclasses.asdict(report.kpis)),
        managers=[OverviewManagerRowOut(**dataclasses.asdict(row)) for row in report.managers],
        needs_attention=[
            OverviewNeedsAttentionOut(**dataclasses.asdict(row))
            for row in report.needs_attention
        ],
    )


@router.get("/{month_id}/program/choices", response_model=ProgramChoicesOut)
def get_program_choices(
    month_id: str,
    business_date: date,
    store_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> ProgramChoicesOut:
    authorize(principal, Capability.SCHEDULE_READ)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    if business_date.year != month.year or business_date.month != month.month:
        raise ScopeError(
            "business date is outside requested month",
            details={"business_date": business_date.isoformat()},
        )
    allowed = effective_store_ids(session, principal, business_date)
    if store_id not in allowed:
        raise ScopeError(
            "requested store is outside manager scope",
            details={"store_id": store_id, "business_date": business_date.isoformat()},
        )
    people = list(
        session.execute(
            select(Person)
            .where(Person.tenant_id == principal.tenant_id, Person.is_active.is_(True))
            .order_by(Person.display_name)
        ).scalars()
    )
    effective_home = effective_home_store_map(
        session,
        tenant_id=principal.tenant_id,
        person_ids={person.id for person in people},
        business_dates={business_date},
    )
    choices = [
        ProgramChoiceOut(
            person_id=person.id,
            display_name=person.display_name,
            home_store_id=effective_home[(person.id, business_date)],
            allowed_store_ids=sorted(allowed),
            working_kinds=[
                WorkingKind.NORMAL,
                WorkingKind.EXTRA_HOME,
                WorkingKind.EXTRA_OTHER,
            ],
        )
        for person in people
        if effective_home.get((person.id, business_date)) in allowed
    ]
    return ProgramChoicesOut(
        month_id=month.id,
        business_date=business_date,
        store_id=store_id,
        choices=choices,
    )


@router.get("/{month_id}/program", response_model=ProgramGridOut)
def get_program(
    month_id: str,
    perspective: str = "stores",
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> ProgramGridOut:
    authorize(principal, Capability.SCHEDULE_READ)
    if perspective not in {"stores", "people"}:
        raise ScopeError("perspective must be 'stores' or 'people'")
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    grid = ProgramService(session).month_grid(
        tenant_id=principal.tenant_id,
        month=month,
        perspective=perspective,
        manager_scope_store_ids=_scope_or_admin(session, principal, month),
    )
    return ProgramGridOut(
        month_id=grid.month_id,
        year=grid.year,
        month=grid.month,
        revision=grid.revision,
        dates=list(grid.dates),
        rows=[
            ProgramRowOut(
                row_id=row.row_id,
                label=row.label,
                home_store_id=row.home_store_id,
                cells=[ProgramCellOut(**dataclasses.asdict(cell)) for cell in row.cells],
            )
            for row in grid.rows
        ],
        legend=list(grid.legend),
    )


@router.post("/{month_id}/program/cell", response_model=CalendarProjectionOut)
def post_program_cell(
    month_id: str,
    payload: CalendarCellEditIn,
    expected_revision: int,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> CalendarProjectionOut:
    """Edit one calendar cell using the same CAS service as XLSX import."""

    authorize(principal, Capability.SCHEDULE_WRITE)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    if month.state == MonthState.CLOSED.value:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "MONTH_CLOSED",
                "message": "month is closed; reopen before editing",
                "details": {"month_id": month.id},
            },
        )
    allowed_by_date = _allowed_by_date(session, principal, month)
    changes = [
        CalendarChange(
            person_id=payload.person_id,
            business_date=payload.business_date,
            store_id=payload.store_id,
            status=payload.status,
            working_kind=payload.working_kind,
        )
    ]
    try:
        result = CalendarService(session).apply(
            month=month,
            tenant_id=principal.tenant_id,
            changes=changes,
            expected_revision=expected_revision,
            allowed_store_ids_by_date=allowed_by_date,
            actor_id=principal.user_id,
            source="API_PROGRAM_CELL",
        )
    except DomainError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    return CalendarProjectionOut(
        month_id=result.month_id,
        revision=result.revision,
        assignment_count=len(result.assignments),
        person_calendar_count=len(result.person_calendar),
        coverage_count=len(result.coverage),
        pontaj_count=len(result.pontaj),
    )


@router.get("/{month_id}/exceptions", response_model=list[ExceptionOut])
def get_exceptions(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[ExceptionOut]:
    authorize(principal, Capability.SCHEDULE_READ)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    entries = ExceptionService(session).month_exceptions(
        tenant_id=principal.tenant_id,
        month=month,
        manager_scope_store_ids=_scope_or_admin(session, principal, month),
    )
    return [ExceptionOut(**dataclasses.asdict(entry)) for entry in entries]


@router.get("/{month_id}/close-checklist", response_model=CloseChecklistOut)
def get_close_checklist(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> CloseChecklistOut:
    authorize(principal, Capability.MONTH_READ)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    checklist = PolicyCloseChecklistService(session).month_checklist(
        tenant_id=principal.tenant_id,
        month=month,
    )
    return CloseChecklistOut(
        month_id=checklist.month_id,
        revision=checklist.revision,
        state=checklist.state,
        blockers=[ChecklistItemOut(**dataclasses.asdict(item)) for item in checklist.blockers],
        generated_at=checklist.generated_at,
        export_summary=list(checklist.export_summary),
        job_summary=list(checklist.job_summary),
        expected_revision=checklist.expected_revision,
    )


@router.post("/{month_id}/reopen-admin", response_model=CloseChecklistOut)
def post_reopen(
    month_id: str,
    payload: ReopenWithReasonIn,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> CloseChecklistOut:
    """Admin-only reopen with mandatory reason and refreshed policy checklist."""

    authorize(principal, Capability.MONTH_REOPEN)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    try:
        PolicyCloseService(session).reopen_month(
            tenant_id=principal.tenant_id,
            month_id=month_id,
            request=ReopenRequest(
                actor_id=principal.user_id,
                role_value=principal.role.value,
                reason=payload.reason,
            ),
        )
    except DomainError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    session.refresh(month)
    checklist = PolicyCloseChecklistService(session).month_checklist(
        tenant_id=principal.tenant_id,
        month=month,
    )
    return CloseChecklistOut(
        month_id=checklist.month_id,
        revision=checklist.revision,
        state=checklist.state,
        blockers=[ChecklistItemOut(**dataclasses.asdict(item)) for item in checklist.blockers],
        generated_at=checklist.generated_at,
        export_summary=list(checklist.export_summary),
        job_summary=list(checklist.job_summary),
        expected_revision=checklist.expected_revision,
    )


@router.get("/{month_id}/pontaj-totals")
def get_pontaj_totals(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> dict[str, object]:
    """Per-person monthly Pontaj totals filtered by effective home-store scope."""

    authorize(principal, Capability.SCHEDULE_READ)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    allowed = _allowed_by_date(session, principal, month)
    rows = list(
        session.execute(
            select(PontajProjection).where(
                PontajProjection.tenant_id == principal.tenant_id,
                PontajProjection.month_id == month.id,
                PontajProjection.revision == month.revision,
            )
        ).scalars()
    )
    effective_home = effective_home_store_map(
        session,
        tenant_id=principal.tenant_id,
        person_ids={row.person_id for row in rows},
        business_dates={row.business_date for row in rows},
    )
    by_person: dict[str, dict[str, float | int]] = {}
    for row in rows:
        home_store = effective_home.get((row.person_id, row.business_date))
        if home_store is None or home_store not in allowed.get(row.business_date, set()):
            continue
        bucket = by_person.setdefault(
            row.person_id,
            {"working_days": 0, "leave_days": 0, "off_days": 0, "hours": 0.0},
        )
        if row.status == DayStatus.WORKING.value:
            bucket["working_days"] = int(bucket["working_days"]) + 1
            bucket["hours"] = float(bucket["hours"]) + float(row.hours)
        elif row.status == DayStatus.LEAVE.value:
            bucket["leave_days"] = int(bucket["leave_days"]) + 1
        else:
            bucket["off_days"] = int(bucket["off_days"]) + 1
    return {"month_id": month.id, "revision": month.revision, "totals": by_person}


__all__ = ["router"]
