"""Persistent Pontaj projection: full lattice, immutability, XLSX path."""

from __future__ import annotations

from datetime import date
from io import BytesIO

import pytest

from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.domain.errors import ConflictError, ScopeError
from ugrile.repositories.models import Month, PontajProjection, ScheduleImportContract
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.services.schedule_contract import consume_contract, issue_contract, validate_contract
from ugrile.services.schedule_xlsx import build_template, parse_schedule

AUGUST_DAYS = 31


def _full_scope(faker_tenant) -> dict[date, set[str]]:
    return {
        date(2026, 8, d): {faker_tenant["store_id"], faker_tenant["other_store_id"]}
        for d in range(1, 32)
    }


def _open_month(session, faker_tenant):
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    return month


def _rows(session, faker_tenant, month, revision: int) -> list[PontajProjection]:
    session.flush()  # the test session runs with autoflush=False
    return list(
        session.query(PontajProjection)
        .filter_by(tenant_id=faker_tenant["tenant_id"], month_id=month.id, revision=revision)
        .order_by(PontajProjection.person_id, PontajProjection.business_date)
        .all()
    )


def test_apply_materializes_full_lattice_with_off_and_leave(session, faker_tenant):
    month = _open_month(session, faker_tenant)
    svc = CalendarService(session)
    svc.apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                faker_tenant["person_a_id"],
                date(2026, 8, 1),
                faker_tenant["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            ),
            CalendarChange(faker_tenant["person_b_id"], date(2026, 8, 2), None, DayStatus.LEAVE),
            CalendarChange(faker_tenant["person_c_id"], date(2026, 8, 3), None, DayStatus.OFF),
        ],
    )
    rows = _rows(session, faker_tenant, month, 1)
    assert len(rows) == 3 * AUGUST_DAYS  # active people x every day of the month
    by_key = {(r.person_id, r.business_date): r for r in rows}

    working = by_key[(faker_tenant["person_a_id"], date(2026, 8, 1))]
    assert working.status == DayStatus.WORKING.value
    assert working.hours == 11
    assert working.start_time.hour == 10 and working.end_time.hour == 22
    assert working.pause_minutes == 60

    leave = by_key[(faker_tenant["person_b_id"], date(2026, 8, 2))]
    assert leave.status == DayStatus.LEAVE.value
    assert leave.hours == 0
    assert leave.start_time is None and leave.end_time is None

    off = by_key[(faker_tenant["person_c_id"], date(2026, 8, 3))]
    assert off.status == DayStatus.OFF.value
    assert off.hours == 0

    # A day with no calendar entry is OFF, not missing.
    untouched = by_key[(faker_tenant["person_a_id"], date(2026, 8, 10))]
    assert untouched.status == DayStatus.OFF.value
    assert untouched.hours == 0


def test_reapply_keeps_previous_revision_rows_immutable(session, faker_tenant):
    month = _open_month(session, faker_tenant)
    svc = CalendarService(session)
    svc.apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                faker_tenant["person_a_id"],
                date(2026, 8, 1),
                faker_tenant["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    revision1_before = {
        (r.person_id, r.business_date, r.status, float(r.hours))
        for r in _rows(session, faker_tenant, month, 1)
    }
    assert len(revision1_before) == 3 * AUGUST_DAYS

    # Mid-month edit: move person_a's working day from day 1 to day 2.
    # The whole-calendar CAS applies on top of the current revision, so the
    # edit must explicitly free day 1 as well.
    svc.apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=1,
        changes=[
            CalendarChange(
                faker_tenant["person_a_id"],
                date(2026, 8, 1),
                None,
                DayStatus.OFF,
            ),
            CalendarChange(
                faker_tenant["person_a_id"],
                date(2026, 8, 2),
                faker_tenant["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            ),
        ],
    )
    revision1_after = {
        (r.person_id, r.business_date, r.status, float(r.hours))
        for r in _rows(session, faker_tenant, month, 1)
    }
    revision2 = {
        (r.person_id, r.business_date, r.status, float(r.hours))
        for r in _rows(session, faker_tenant, month, 2)
    }
    assert revision1_after == revision1_before  # revision 1 rows never change
    assert len(revision2) == 3 * AUGUST_DAYS
    day1 = next(
        r for r in revision2 if r[1] == date(2026, 8, 1) and r[0] == faker_tenant["person_a_id"]
    )
    day2 = next(
        r for r in revision2 if r[1] == date(2026, 8, 2) and r[0] == faker_tenant["person_a_id"]
    )
    assert day1[2] == DayStatus.OFF.value
    assert day2[2] == DayStatus.WORKING.value
    assert day2[3] == 11.0


def test_xlsx_apply_materializes_and_consumes_contract(session, faker_tenant):
    month = _open_month(session, faker_tenant)
    stores = {"s1": faker_tenant["store_id"]}
    people = [
        {
            "person_id": faker_tenant["person_a_id"],
            "display_name": "Alice",
            "home_store_code": "s1",
            "manager_code": "m1",
        }
    ]
    allowed_by_date = _full_scope(faker_tenant)
    token = issue_contract(
        session,
        tenant_id=faker_tenant["tenant_id"],
        month=month,
        user_id="user_admin",
        base_revision=0,
        stores=stores,
        people=people,
        allowed_by_date=allowed_by_date,
    )
    data = build_template(
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        year=2026,
        month=8,
        base_revision=0,
        contract_token=token,
        people=people,
        stores=stores,
        calendar=[
            CalendarChange(
                faker_tenant["person_a_id"],
                date(2026, 8, 1),
                faker_tenant["store_id"],
                DayStatus.WORKING,
            )
        ],
        allowed_store_ids_by_date=allowed_by_date,
    )
    parsed = parse_schedule(
        BytesIO(data).getvalue(),
        expected_tenant_id=faker_tenant["tenant_id"],
        expected_month_id=month.id,
        year=2026,
        month=8,
        stores=stores,
    )
    assert parsed.errors == []
    assert parsed.contract_token == token
    contract = validate_contract(
        session,
        token=parsed.contract_token,
        tenant_id=faker_tenant["tenant_id"],
        month=month,
        user_id="user_admin",
        parsed_tenant_id=parsed.tenant_id,
        parsed_month_id=parsed.month_id,
        parsed_revision=parsed.base_revision,
        stores=stores,
        people=people,
        allowed_by_date=allowed_by_date,
        lock=True,
    )
    result = CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        changes=parsed.changes,
        expected_revision=contract.base_revision,
        allowed_store_ids_by_date=allowed_by_date,
    )
    consume_contract(session, contract)
    assert result.revision == 1
    # The lattice covers every active person of the tenant, not only the
    # people present in the uploaded workbook.
    assert len(_rows(session, faker_tenant, month, 1)) == 3 * AUGUST_DAYS

    # Replay of the same workbook is rejected after consumption.
    with pytest.raises(ConflictError) as exc:
        validate_contract(
            session,
            token=parsed.contract_token,
            tenant_id=faker_tenant["tenant_id"],
            month=month,
            user_id="user_admin",
            parsed_tenant_id=parsed.tenant_id,
            parsed_month_id=parsed.month_id,
            parsed_revision=parsed.base_revision,
            stores=stores,
            people=people,
            allowed_by_date=allowed_by_date,
            lock=False,
        )
    assert exc.value.details["code"] == "CONTRACT_CONSUMED"


def test_failed_apply_writes_nothing_and_keeps_contract(session, faker_tenant):
    month = _open_month(session, faker_tenant)
    stores = {"s1": faker_tenant["store_id"], "s2": faker_tenant["other_store_id"]}
    people = [
        {
            "person_id": faker_tenant["person_c_id"],
            "display_name": "Carmen",
            "home_store_code": "s2",
            "manager_code": "m1",
        }
    ]
    allowed_by_date = _full_scope(faker_tenant)
    token = issue_contract(
        session,
        tenant_id=faker_tenant["tenant_id"],
        month=month,
        user_id="user_admin",
        base_revision=0,
        stores=stores,
        people=people,
        allowed_by_date=allowed_by_date,
    )
    session.commit()  # the template request persisted the contract row
    session.expire_all()
    contract = validate_contract(
        session,
        token=token,
        tenant_id=faker_tenant["tenant_id"],
        month=month,
        user_id="user_admin",
        parsed_tenant_id=faker_tenant["tenant_id"],
        parsed_month_id=month.id,
        parsed_revision=0,
        stores=stores,
        people=people,
        allowed_by_date=allowed_by_date,
        lock=True,
    )
    # The manager scope shrank after issuance: only s1 is allowed now.
    narrowed = {date(2026, 8, d): {faker_tenant["store_id"]} for d in range(1, 32)}
    with pytest.raises(ScopeError):
        CalendarService(session).apply(
            month=month,
            tenant_id=faker_tenant["tenant_id"],
            changes=[
                CalendarChange(
                    faker_tenant["person_c_id"],
                    date(2026, 8, 1),
                    faker_tenant["other_store_id"],
                    DayStatus.WORKING,
                    WorkingKind.NORMAL,
                )
            ],
            expected_revision=contract.base_revision,
            allowed_store_ids_by_date=narrowed,
        )
    session.rollback()
    assert session.get(Month, month.id).revision == 0
    assert session.query(PontajProjection).count() == 0
    persisted = session.query(ScheduleImportContract).one()
    assert persisted.consumed_at is None
