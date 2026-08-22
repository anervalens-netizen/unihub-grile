"""Contract-bound XLSX schedule template, preview, apply and Pontaj endpoints."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal
from io import BytesIO

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..api.schemas import (
    CalendarProjectionOut,
    PontajMonthOut,
    PontajPersonTotalsOut,
    PontajRowOut,
    SchedulePreviewOut,
)
from ..domain.enums import DayStatus, WorkingKind
from ..domain.errors import DomainError, ValidationError
from ..repositories.models import (
    Month,
    Person,
    PersonDayAbsence,
    ScheduleImportContract,
    Store,
)
from ..repositories.models import SiteDayAssignment as AssignmentRow
from ..repositories.months import MonthRepository
from ..repositories.pontaj import PontajRepository
from ..services.auth import Principal, assert_same_tenant, effective_store_ids
from ..services.authorization import Capability, authorize
from ..services.calendar import CalendarChange, CalendarService
from ..services.person_scope import effective_home_store_map
from ..services.schedule_contract import consume_contract, issue_contract, validate_contract
from ..services.schedule_xlsx import ParsedSchedule, build_template, parse_schedule

router = APIRouter(prefix="/months", tags=["schedule"])
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _month_dates(month: Month) -> list[date]:
    return [
        date(month.year, month.month, day)
        for day in range(1, monthrange(month.year, month.month)[1] + 1)
    ]


def _allowed_by_date(
    session: Session,
    principal: Principal,
    month: Month,
) -> dict[date, set[str]]:
    return {
        business_date: effective_store_ids(session, principal, business_date)
        for business_date in _month_dates(month)
    }


def _catalog_for_scope(
    session: Session,
    principal: Principal,
    month: Month,
) -> tuple[dict[str, str], list[dict[str, str]], dict[date, set[str]]]:
    """Build an effective-dated, fail-closed XLSX person/store catalog.

    The current XLSX schema has one display/default ``home_store_code`` per
    person row. A person is included when their effective home belongs to the
    caller on at least one day in the month, but that single display value is
    emitted only when the effective home is known and identical on *every*
    business date. Transfers or assignment-history gaps therefore render the
    whole row as ``BLOCAT`` instead of pretending one mutable catalog home is
    authoritative. Direct UI/calendar writes continue to support per-day
    effective homes.

    ``effective_home_fingerprint`` is server-only contract material. It is not
    rendered into the workbook; hashing it makes any dated assignment drift
    invalidate an outstanding import contract.
    """

    month_dates = _month_dates(month)
    allowed_by_date = _allowed_by_date(session, principal, month)
    allowed_store_ids = set().union(*allowed_by_date.values()) if allowed_by_date else set()
    stores = list(
        session.execute(
            select(Store)
            .where(
                Store.tenant_id == principal.tenant_id,
                Store.id.in_(allowed_store_ids),
            )
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
                Person.is_active.is_(True),
            )
            .order_by(Person.internal_code)
        ).scalars()
    )
    effective_home = effective_home_store_map(
        session,
        tenant_id=principal.tenant_id,
        person_ids={person.id for person in people},
        business_dates=set(month_dates),
    )

    people_rows: list[dict[str, str]] = []
    for person in people:
        resolved = [effective_home.get((person.id, business_date)) for business_date in month_dates]
        if not any(
            home_store_id is not None
            and home_store_id in allowed_by_date.get(business_date, set())
            for business_date, home_store_id in zip(month_dates, resolved, strict=True)
        ):
            continue

        distinct_homes = {home_store_id for home_store_id in resolved if home_store_id is not None}
        stable_home_id = (
            next(iter(distinct_homes))
            if len(distinct_homes) == 1 and all(home_store_id is not None for home_store_id in resolved)
            else None
        )
        home_store_code = (
            store_by_id[stable_home_id].internal_code
            if stable_home_id is not None and stable_home_id in store_by_id
            else ""
        )
        fingerprint = "|".join(
            f"{business_date.isoformat()}={home_store_id or ''}"
            for business_date, home_store_id in zip(month_dates, resolved, strict=True)
        )
        people_rows.append(
            {
                "person_id": person.id,
                "display_name": person.display_name,
                "home_store_code": home_store_code,
                "manager_code": principal.user_id,
                "effective_home_fingerprint": fingerprint,
            }
        )
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
    business_dates = {row.business_date for row in assignments}
    business_dates.update(row.business_date for row in absences)
    effective_home = effective_home_store_map(
        session,
        tenant_id=tenant_id,
        person_ids=person_ids,
        business_dates=business_dates,
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
        and effective_home.get((row.person_id, row.business_date))
        in allowed_by_date[row.business_date]
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
        and effective_home.get((row.person_id, row.business_date))
        in allowed_by_date[row.business_date]
    )
    return changes


@router.get("/{month_id}/schedule/template")
def schedule_template(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> StreamingResponse:
    authorize(principal, Capability.SCHEDULE_READ)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    stores, people, allowed_by_date = _catalog_for_scope(session, principal, month)
    if not stores or not people:
        raise ValidationError(
            "manager has no effective store/person scope",
            details={"month_id": month.id},
        )
    token = issue_contract(
        session,
        tenant_id=principal.tenant_id,
        month=month,
        user_id=principal.user_id,
        base_revision=month.revision,
        stores=stores,
        people=people,
        allowed_by_date=allowed_by_date,
    )
    data = build_template(
        tenant_id=principal.tenant_id,
        month_id=month.id,
        year=month.year,
        month=month.month,
        base_revision=month.revision,
        contract_token=token,
        people=people,
        stores=stores,
        calendar=_calendar_for_scope(
            session,
            principal.tenant_id,
            month,
            people,
            allowed_by_date,
        ),
        allowed_store_ids_by_date=allowed_by_date,
    )
    return StreamingResponse(
        BytesIO(data),
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": (
                f'attachment; filename="schedule-{month.year}-{month.month:02d}.xlsx"'
            )
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


def _validate_contract_for_upload(
    session: Session,
    *,
    parsed: ParsedSchedule,
    principal: Principal,
    month: Month,
    stores: dict[str, str],
    people: list[dict[str, str]],
    allowed_by_date: dict[date, set[str]],
    lock: bool,
) -> ScheduleImportContract:
    return validate_contract(
        session,
        token=parsed.contract_token,
        tenant_id=principal.tenant_id,
        month=month,
        user_id=principal.user_id,
        parsed_tenant_id=parsed.tenant_id,
        parsed_month_id=parsed.month_id,
        parsed_revision=parsed.base_revision,
        stores=stores,
        people=people,
        allowed_by_date=allowed_by_date,
        lock=lock,
    )


@router.post("/{month_id}/schedule/preview", response_model=SchedulePreviewOut)
async def schedule_preview(
    month_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> SchedulePreviewOut:
    authorize(principal, Capability.SCHEDULE_READ)
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
    warnings = list(parsed.warnings)
    try:
        _validate_contract_for_upload(
            session,
            parsed=parsed,
            principal=principal,
            month=month,
            stores=stores,
            people=people,
            allowed_by_date=allowed_by_date,
            lock=False,
        )
    except DomainError as exc:
        errors.append(
            {"code": exc.code, "message": exc.message, "details": exc.details}
        )
    if not errors:
        try:
            result = CalendarService(session).preview(
                month=month,
                tenant_id=principal.tenant_id,
                changes=parsed.changes,
                expected_revision=parsed.base_revision,
                allowed_store_ids_by_date=allowed_by_date,
                actor_id=principal.user_id,
                source="XLSX_PREVIEW",
            )
        except DomainError as exc:
            errors.append(
                {"code": exc.code, "message": exc.message, "details": exc.details}
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
    authorize(principal, Capability.SCHEDULE_WRITE)
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
    if parsed.errors:
        raise ValidationError(
            "schedule contains blocking errors",
            details={"errors": parsed.errors},
        )
    contract = _validate_contract_for_upload(
        session,
        parsed=parsed,
        principal=principal,
        month=month,
        stores=stores,
        people=people,
        allowed_by_date=allowed_by_date,
        lock=True,
    )
    result = CalendarService(session).apply(
        month=month,
        tenant_id=principal.tenant_id,
        changes=parsed.changes,
        expected_revision=contract.base_revision,
        allowed_store_ids_by_date=allowed_by_date,
        actor_id=principal.user_id,
        source="XLSX_IMPORT",
    )
    consume_contract(session, contract)
    return CalendarProjectionOut(
        month_id=result.month_id,
        revision=result.revision,
        assignment_count=len(result.assignments),
        person_calendar_count=len(result.person_calendar),
        coverage_count=len(result.coverage),
        pontaj_count=len(result.pontaj),
    )


@router.get("/{month_id}/pontaj", response_model=PontajMonthOut)
def get_pontaj(
    month_id: str,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> PontajMonthOut:
    """Read latest persistent Pontaj filtered by effective-dated person scope."""

    authorize(principal, Capability.SCHEDULE_READ)
    month = MonthRepository(session).get(month_id)
    assert_same_tenant(principal, month.tenant_id)
    allowed_by_date = _allowed_by_date(session, principal, month)
    repo = PontajRepository(session)
    revision = repo.latest_revision(principal.tenant_id, month.id)
    if revision is None:
        return PontajMonthOut(
            month_id=month.id,
            revision=month.revision,
            rows=[],
            totals=[],
        )
    rows = repo.list_for_revision(principal.tenant_id, month.id, revision)
    effective_home = effective_home_store_map(
        session,
        tenant_id=principal.tenant_id,
        person_ids={row.person_id for row in rows},
        business_dates={row.business_date for row in rows},
    )
    visible = [
        row
        for row in rows
        if effective_home.get((row.person_id, row.business_date))
        in allowed_by_date.get(row.business_date, set())
    ]
    counts: dict[str, dict[str, int]] = {}
    total_hours: dict[str, Decimal] = {}
    for row in visible:
        bucket = counts.setdefault(
            row.person_id,
            {"working_days": 0, "leave_days": 0, "off_days": 0},
        )
        if row.status == DayStatus.WORKING.value:
            bucket["working_days"] += 1
            total_hours[row.person_id] = total_hours.get(
                row.person_id,
                Decimal(0),
            ) + row.hours
        elif row.status == DayStatus.LEAVE.value:
            bucket["leave_days"] += 1
        else:
            bucket["off_days"] += 1
    return PontajMonthOut(
        month_id=month.id,
        revision=revision,
        rows=[
            PontajRowOut(
                person_id=row.person_id,
                business_date=row.business_date,
                status=DayStatus(row.status),
                start_time=row.start_time,
                end_time=row.end_time,
                pause_minutes=row.pause_minutes,
                hours=row.hours,
            )
            for row in visible
        ],
        totals=[
            PontajPersonTotalsOut(
                person_id=person_id,
                working_days=bucket["working_days"],
                leave_days=bucket["leave_days"],
                off_days=bucket["off_days"],
                total_hours=total_hours.get(person_id, Decimal(0)),
            )
            for person_id, bucket in sorted(counts.items())
        ],
    )


__all__ = ["router"]
