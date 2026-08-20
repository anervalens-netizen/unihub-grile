"""S4 Manager UI read/write endpoints.

These endpoints power the manager UI pages (Overview, Program, Magazin,
Agent, Exceptii, Close). They are designed to honour the S4 contract:

* the Overview page reads one aggregate request (no per-store fan-out);
* the Program page returns the full 31-day matrix in one response so the
  client can virtualize rows;
* the Exceptions page returns the typed list sorted by severity;
* the Close page returns the close checklist (typed blockers + revision),
  accepts the reopen with reason (admin-only, >= 4 chars) and exposes the
  audit timeline;
* job state (export/projection) is exposed via the existing worker API.

All read endpoints stay inside the caller's tenant. The manager scope
filter (``effective_store_ids``) is applied so out-of-scope stores never
appear in the response.
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
    ProgramGridOut,
    ProgramRowOut,
    ReopenWithReasonIn,
)
from ..domain.enums import DayStatus, MonthState
from ..domain.errors import DomainError, ScopeError
from ..repositories.models import Month, PontajProjection, SiteDayAssignment
from ..repositories.months import MonthRepository
from ..services.auth import (
    Principal,
    assert_admin,
    assert_manager_or_admin,
    assert_same_tenant,
    effective_store_ids,
)
from ..services.calendar import CalendarChange, CalendarService
from ..services.close import CloseService, ReopenRequest
from ..services.overview import (
    CloseChecklistService,
    ExceptionService,
    OverviewService,
    ProgramService,
)

router = APIRouter(prefix="/months", tags=["s4-manager-ui"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _allowed_by_date(session: Session, principal: Principal, month: Month) -> dict[date, set[str]]:
    days = monthrange(month.year, month.month)[1]
    return {
        date(month.year, month.month, 1 + offset): effective_store_ids(
            session, principal, date(month.year, month.month, 1 + offset)
        )
        for offset in range(days)
    }


def _scope_or_admin(session: Session, principal: Principal, month: Month) -> Mapping[date, set[str]] | None:
    if principal.role.value == "ADMIN":
        return None
    return _allowed_by_date(session, principal, month)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@router.get("/{month_id}/overview", response_model=OverviewOut)
def get_overview(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> OverviewOut:
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
        managers=[
            OverviewManagerRowOut(**dataclasses.asdict(row)) for row in report.managers
        ],
        needs_attention=[
            OverviewNeedsAttentionOut(**dataclasses.asdict(row))
            for row in report.needs_attention
        ],
    )


# ---------------------------------------------------------------------------
# Program (per magazine / per agenti)
# ---------------------------------------------------------------------------


@router.get("/{month_id}/program", response_model=ProgramGridOut)
def get_program(
    month_id: str,
    perspective: str = "stores",
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> ProgramGridOut:
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
    """Edit a single calendar cell (click-cell → choose agent + classification).

    The atomic CAS is the same :class:`CalendarService` used by the XLSX
    import; ``expected_revision`` keeps the manager UI aligned with the
    server revision and produces a 409 on a stale client.
    """

    assert_manager_or_admin(principal)
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
        )
    except DomainError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        ) from exc
    return CalendarProjectionOut(
        month_id=result.month_id,
        revision=result.revision,
        assignment_count=len(result.assignments),
        person_calendar_count=len(result.person_calendar),
        coverage_count=len(result.coverage),
        pontaj_count=len(result.pontaj),
    )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


@router.get("/{month_id}/exceptions", response_model=list[ExceptionOut])
def get_exceptions(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[ExceptionOut]:
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    entries = ExceptionService(session).month_exceptions(
        tenant_id=principal.tenant_id,
        month=month,
        manager_scope_store_ids=_scope_or_admin(session, principal, month),
    )
    return [ExceptionOut(**dataclasses.asdict(entry)) for entry in entries]


# ---------------------------------------------------------------------------
# Close checklist + reopen
# ---------------------------------------------------------------------------


@router.get("/{month_id}/close-checklist", response_model=CloseChecklistOut)
def get_close_checklist(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> CloseChecklistOut:
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    checklist = CloseChecklistService(session).month_checklist(
        tenant_id=principal.tenant_id, month=month
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
    """Admin-only reopen with a reason (>= 4 chars).

    Returns the new close checklist so the manager UI can refresh the
    state and the blocker list in one round trip.
    """

    assert_admin(principal)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    try:
        CloseService(session).reopen_month(
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
            detail={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        ) from exc
    # Refresh the locked month so the checklist reflects the new state.
    session.refresh(month)
    checklist = CloseChecklistService(session).month_checklist(
        tenant_id=principal.tenant_id, month=month
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


# ---------------------------------------------------------------------------
# Pontaj (read-only, per month for the manager page)
# ---------------------------------------------------------------------------


@router.get("/{month_id}/pontaj-totals")
def get_pontaj_totals(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> dict[str, object]:
    """Per-person monthly Pontaj totals (hours + day counts).

    The full per-day projection lives in
    :func:`ugrile.api.schedule.get_pontaj`. The totals endpoint is the
    summary used by the Magazin and Agent pages.
    """

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
    by_person: dict[str, dict[str, float | int]] = {}
    for row in rows:
        person = session.execute(
            select(SiteDayAssignment).where(
                SiteDayAssignment.tenant_id == principal.tenant_id,
                SiteDayAssignment.month_id == month.id,
                SiteDayAssignment.person_id == row.person_id,
                SiteDayAssignment.business_date == row.business_date,
            )
        ).scalars().first()
        home_store = person.store_id if person else None
        if home_store and home_store not in allowed.get(row.business_date, set()):
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
