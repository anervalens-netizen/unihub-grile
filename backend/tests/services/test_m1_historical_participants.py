from __future__ import annotations

from datetime import date

from sqlalchemy import select

from ugrile.domain.enums import CloseBlockerCode, DayStatus, MonthState, WorkingKind
from ugrile.repositories.models import Person, PontajProjection, StoreAssignment
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.services.close import CloseService
from ugrile.services.month_participants import month_participant_ids
from ugrile.services.payroll_grid import PayrollGridService
from ugrile.services.policy_close import PolicyCloseService


def _open_august(session, faker_tenant):
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    return month


def test_inactive_leaver_remains_in_latest_pontaj_after_later_calendar_edit(
    session, faker_tenant
):
    month = _open_august(session, faker_tenant)
    service = CalendarService(session)
    service.apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                person_id=faker_tenant["person_a_id"],
                business_date=date(2026, 8, 10),
                store_id=faker_tenant["store_id"],
                status=DayStatus.WORKING,
                working_kind=WorkingKind.NORMAL,
            )
        ],
    )
    person = session.get(Person, faker_tenant["person_a_id"])
    assert person is not None
    person.is_active = False
    session.flush()

    service.apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=1,
        changes=[
            CalendarChange(
                person_id=faker_tenant["person_b_id"],
                business_date=date(2026, 8, 11),
                store_id=faker_tenant["store_id"],
                status=DayStatus.WORKING,
                working_kind=WorkingKind.NORMAL,
            )
        ],
    )
    session.flush()

    latest = list(
        session.execute(
            select(PontajProjection).where(
                PontajProjection.tenant_id == faker_tenant["tenant_id"],
                PontajProjection.month_id == month.id,
                PontajProjection.person_id == faker_tenant["person_a_id"],
                PontajProjection.revision == 2,
            )
        ).scalars()
    )
    assert len(latest) == 31
    worked = next(row for row in latest if row.business_date == date(2026, 8, 10))
    assert worked.status == "WORKING"


def test_payroll_grid_includes_inactive_month_participant(session, faker_tenant):
    month = _open_august(session, faker_tenant)
    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                person_id=faker_tenant["person_a_id"],
                business_date=date(2026, 8, 10),
                store_id=faker_tenant["store_id"],
                status=DayStatus.WORKING,
                working_kind=WorkingKind.NORMAL,
            )
        ],
    )
    person = session.get(Person, faker_tenant["person_a_id"])
    assert person is not None
    person.is_active = False
    session.flush()

    _, rows = PayrollGridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"],
        month=month,
    )
    assert faker_tenant["person_a_id"] in {row.person_id for row in rows}


def test_close_requires_grid_for_inactive_month_participant(session, faker_tenant):
    month = _open_august(session, faker_tenant)
    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                person_id=faker_tenant["person_a_id"],
                business_date=date(2026, 8, 10),
                store_id=faker_tenant["store_id"],
                status=DayStatus.WORKING,
                working_kind=WorkingKind.NORMAL,
            )
        ],
    )
    person = session.get(Person, faker_tenant["person_a_id"])
    assert person is not None
    person.is_active = False
    session.flush()

    blockers = PolicyCloseService(session)._grid_blockers(
        tenant_id=faker_tenant["tenant_id"],
        month=month,
    )
    assert any(
        blocker.code == CloseBlockerCode.GRID_CURRENT_REVISION_REQUIRED
        and blocker.person_id == faker_tenant["person_a_id"]
        for blocker in blockers
    )


def test_close_working_kind_uses_historical_home_not_current_catalog(session, faker_tenant):
    month = _open_august(session, faker_tenant)
    person = session.get(Person, faker_tenant["person_a_id"])
    assert person is not None
    person.home_store_id = faker_tenant["other_store_id"]
    session.add(
        StoreAssignment(
            tenant_id=faker_tenant["tenant_id"],
            person_id=faker_tenant["person_a_id"],
            store_id=faker_tenant["store_id"],
            effective_from=date(2026, 8, 1),
            effective_to=date(2026, 8, 31),
        )
    )
    session.flush()
    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                person_id=faker_tenant["person_a_id"],
                business_date=date(2026, 8, 10),
                store_id=faker_tenant["store_id"],
                status=DayStatus.WORKING,
                working_kind=WorkingKind.NORMAL,
            )
        ],
    )

    validation = CloseService(session)._validation(
        tenant_id=faker_tenant["tenant_id"],
        month=month,
    )
    assert not any(
        blocker.code == CloseBlockerCode.INVALID_WORKING_KIND
        and blocker.person_id == faker_tenant["person_a_id"]
        and blocker.business_date == date(2026, 8, 10)
        for blocker in validation.blockers
    )


def test_future_store_assignment_does_not_backfill_historical_participation(
    session, faker_tenant
):
    month = _open_august(session, faker_tenant)
    person = session.get(Person, faker_tenant["person_c_id"])
    assert person is not None
    person.is_active = True
    session.add(
        StoreAssignment(
            tenant_id=faker_tenant["tenant_id"],
            person_id=faker_tenant["person_c_id"],
            store_id=faker_tenant["other_store_id"],
            effective_from=date(2026, 9, 1),
            effective_to=None,
        )
    )
    session.flush()

    participants = month_participant_ids(
        session,
        tenant_id=faker_tenant["tenant_id"],
        month=month,
    )
    assert faker_tenant["person_c_id"] not in participants
