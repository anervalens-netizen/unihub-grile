"""S2 XLSX schedule template, preview and atomic apply endpoints."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from io import BytesIO

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..api.schemas import CalendarProjectionOut, SchedulePreviewOut
from ..domain.enums import DayStatus, WorkingKind
from ..domain.errors import DomainError, ValidationError
from ..repositories.models import (
    Month,
    Person,
    PersonDayAbsence,
    Store,
)
from ..repositories.models import (
    SiteDayAssignment as AssignmentRow,
)
from ..repositories.months import MonthRepository
from ..services.auth import (
    Principal,
    assert_manager_or_admin,
    assert_same_tenant,
    effective_store_ids,
)
from ..services.calendar import CalendarChange, CalendarService
from ..services.schedule_xlsx import build_template, parse_schedule

router = APIRouter(prefix="/months", tags=["schedule"])
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _month_dates(month: Month) -> list[date]:
    return [
        date(month.year, month.month, day)
        for day in range(1, monthrange(month.year, month.month)[1] + 1)
    ]


def _allowed_by_date(session: Session, principal: Principal, month: Month) -> dict[date, set[str]]:
    return {day: effective_store_ids(session, principal, day) for day in _month_dates(month)}


def _catalog_for_scope(
    session: Session, principal: Principal, month: Month
) -> tuple[dict[str, str], list[dict[str, str]], dict[date, set[str]]]:
    allowed_by_date = _allowed_by_date(session, principal, month)
    allowed_store_ids = set().union(*allowed_by_date.values()) if allowed_by_date else set()
    stores = list(
        session.execute(
            select(Store)
            .where(Store.tenant_id == principal.tenant_id, Store.id.in_(allowed_store_ids))
            .order_by(Store.internal_code)
        ).scalars()
    )
    store_by_id = {store.id: store for store in stores}
    store_codes = {store.internal_code: store.id for store in stores}
    people = list(
        session.execute(
            select(Person)
            .where(
                Person.tenant_id == principal.tenant_id,
                Person.home_store_id.in_(set(store_by_id)),
            )
            .order_by(Person.internal_code)
        ).scalars()
    )
    people_rows = [
        {
            "person_id": person.id,
            "display_name": person.display_name,
            "home_store_code": store_by_id[person.home_store_id].internal_code,
            "manager_code": principal.user_id,
        }
        for person in people
    ]
    return store_codes, people_rows, allowed_by_date


def _calendar_for_scope(
    session: Session,
    tenant_id: str,
    month: Month,
    people: list[dict[str, str]],
    allowed_by_date: dict[date, set[str]],
) -> list[CalendarChange]:
    person_ids = {row["person_id"] for row in people}
    assignments = list(
        session.execute(
            select(AssignmentRow).where(
                AssignmentRow.tenant_id == tenant_id,
                AssignmentRow.month_id == month.id,
                AssignmentRow.person_id.in_(person_ids),
            )
        ).scalars()
    )
    absences = list(
        session.execute(
            select(PersonDayAbsence).where(
                PersonDayAbsence.tenant_id == tenant_id,
                PersonDayAbsence.month_id == month.id,
                PersonDayAbsence.person_id.in_(person_ids),
            )
        ).scalars()
    )
    changes = [
        CalendarChange(
            person_id=row.person_id,
            business_date=row.business_date,
            store_id=row.store_id,
            status=DayStatus(row.status),
            working_kind=WorkingKind(row.working_kind) if row.working_kind else None,
        )
        for row in assignments
        if row.business_date in allowed_by_date
        and row.store_id in allowed_by_date[row.business_date]
    ]
    changes.extend(
        CalendarChange(
            person_id=row.person_id,
            business_date=row.business_date,
            store_id=None,
            status=DayStatus(row.status),
        )
        for row in absences
        if row.business_date in allowed_by_date
    )
    return changes


@router.get("/{month_id}/schedule/template")
def schedule_template(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> StreamingResponse:
    assert_manager_or_admin(principal)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    stores, people, allowed_by_date = _catalog_for_scope(session, principal, month)
    if not stores or not people:
        raise ValidationError(
            "manager has no effective store/person scope",
            details={"month_id": month.id},
        )
    data = build_template(
        tenant_id=principal.tenant_id,
        month_id=month.id,
        year=month.year,
        month=month.month,
        base_revision=month.revision,
        people=people,
        stores=stores,
        calendar=_calendar_for_scope(session, principal.tenant_id, month, people, allowed_by_date),
    )
    return StreamingResponse(
        BytesIO(data),
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f'attachment; filename="schedule-{month.year}-{month.month:02d}.xlsx"'
        },
    )


async def _read_upload(upload: UploadFile) -> bytes:
    if upload.content_type not in {None, XLSX_MEDIA_TYPE, "application/octet-stream"}:
        raise HTTPException(status_code=422, detail={"code": "INVALID_XLSX_TYPE"})
    data = await upload.read()
    if not data:
        raise HTTPException(status_code=422, detail={"code": "EMPTY_XLSX"})
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail={"code": "XLSX_TOO_LARGE"})
    return data


@router.post("/{month_id}/schedule/preview", response_model=SchedulePreviewOut)
async def schedule_preview(
    month_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> SchedulePreviewOut:
    assert_manager_or_admin(principal)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    stores, people, allowed_by_date = _catalog_for_scope(session, principal, month)
    known_people = {row["person_id"] for row in people}
    parsed = parse_schedule(
        await _read_upload(file),
        expected_tenant_id=principal.tenant_id,
        expected_month_id=month.id,
        year=month.year,
        month=month.month,
        stores=stores,
        known_person_ids=known_people,
    )
    errors = list(parsed.errors)
    if parsed.base_revision != month.revision:
        errors.append(
            {
                "code": "STALE_REVISION",
                "expected": month.revision,
                "provided": parsed.base_revision,
            }
        )
    warnings = list(parsed.warnings)
    if not errors:
        try:
            result = CalendarService(session).preview(
                month=month,
                tenant_id=principal.tenant_id,
                changes=parsed.changes,
                expected_revision=parsed.base_revision,
                allowed_store_ids_by_date=allowed_by_date,
            )
        except DomainError as exc:
            errors.append(
                {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            )
        else:
            warnings.extend(
                {
                    "code": "STORE_DAY_UNCOVERED",
                    "store_id": coverage.store_id,
                    "business_date": coverage.business_date.isoformat(),
                }
                for coverage in result.coverage
                if not coverage.covered
            )
    return SchedulePreviewOut(
        base_revision=parsed.base_revision,
        changes=len(parsed.changes),
        errors=errors,
        warnings=warnings,
    )


@router.post("/{month_id}/schedule/apply", response_model=CalendarProjectionOut)
async def schedule_apply(
    month_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> CalendarProjectionOut:
    assert_manager_or_admin(principal)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    stores, people, allowed_by_date = _catalog_for_scope(session, principal, month)
    parsed = parse_schedule(
        await _read_upload(file),
        expected_tenant_id=principal.tenant_id,
        expected_month_id=month.id,
        year=month.year,
        month=month.month,
        stores=stores,
        known_person_ids={row["person_id"] for row in people},
    )
    errors = list(parsed.errors)
    if parsed.base_revision != month.revision:
        errors.append(
            {
                "code": "STALE_REVISION",
                "expected": month.revision,
                "provided": parsed.base_revision,
            }
        )
    if errors:
        raise ValidationError(
            "schedule preview contains blocking errors", details={"errors": errors}
        )
    result = CalendarService(session).apply(
        month=month,
        tenant_id=principal.tenant_id,
        changes=parsed.changes,
        expected_revision=parsed.base_revision,
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
