from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select

from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.repositories.models import AuditEvent, SiteDayAssignment
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService


class _RollbackProbe(Exception):
    pass


def _working_change(faker_tenant, *, day: int = 10) -> CalendarChange:
    return CalendarChange(
        person_id=faker_tenant["person_a_id"],
        business_date=date(2026, 8, day),
        store_id=faker_tenant["store_id"],
        status=DayStatus.WORKING,
        working_kind=WorkingKind.NORMAL,
    )


def _audit_rows(session) -> list[AuditEvent]:
    return list(session.execute(select(AuditEvent).order_by(AuditEvent.id)).scalars())


def test_calendar_apply_records_actor_revision_source_and_before_after(session, faker_tenant):
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()

    service = CalendarService(session)
    service.apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=0,
        changes=[_working_change(faker_tenant)],
        actor_id="user_manager",
        source="API_CALENDAR",
        correlation_id="corr-first",
    )
    service.apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=1,
        changes=[
            CalendarChange(
                person_id=faker_tenant["person_a_id"],
                business_date=date(2026, 8, 10),
                store_id=None,
                status=DayStatus.OFF,
                working_kind=None,
            )
        ],
        actor_id="user_manager",
        source="API_CALENDAR",
        correlation_id="corr-second",
    )
    session.flush()

    events = _audit_rows(session)
    assert len(events) == 2
    first_payload = json.loads(events[0].payload)
    second_payload = json.loads(events[1].payload)

    assert events[0].actor_id == "user_manager"
    assert events[0].action == "CALENDAR_APPLY"
    assert events[0].entity == "month"
    assert events[0].entity_id == month.id
    assert first_payload["revision_before"] == 0
    assert first_payload["revision_after"] == 1
    assert first_payload["source"] == "API_CALENDAR"
    assert first_payload["correlation_id"] == "corr-first"
    assert first_payload["changes"][0]["before"] is None
    assert first_payload["changes"][0]["after"]["status"] == "WORKING"

    assert second_payload["revision_before"] == 1
    assert second_payload["revision_after"] == 2
    assert second_payload["correlation_id"] == "corr-second"
    assert second_payload["changes"][0]["before"]["status"] == "WORKING"
    assert second_payload["changes"][0]["before"]["store_id"] == faker_tenant["store_id"]
    assert second_payload["changes"][0]["after"]["status"] == "OFF"
    assert second_payload["changes"][0]["after"]["store_id"] is None


def test_calendar_preview_leaves_no_audit_or_business_mutation(session, faker_tenant):
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()

    result = CalendarService(session).preview(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=0,
        changes=[_working_change(faker_tenant)],
        actor_id="user_manager",
        source="XLSX_PREVIEW",
        correlation_id="corr-preview",
    )
    assert result.revision == 1

    session.refresh(month)
    assert month.revision == 0
    assert _audit_rows(session) == []
    assignments = list(
        session.execute(
            select(SiteDayAssignment).where(SiteDayAssignment.month_id == month.id)
        ).scalars()
    )
    assert assignments == []


def test_calendar_audit_and_business_rows_roll_back_together(session, faker_tenant):
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()

    try:
        with session.begin_nested():
            CalendarService(session).apply(
                month=month,
                tenant_id=faker_tenant["tenant_id"],
                expected_revision=0,
                changes=[_working_change(faker_tenant)],
                actor_id="user_manager",
                source="API_CALENDAR",
                correlation_id="corr-rollback",
            )
            assert len(_audit_rows(session)) == 1
            raise _RollbackProbe
    except _RollbackProbe:
        pass

    session.refresh(month)
    assert month.revision == 0
    assert _audit_rows(session) == []
    assignments = list(
        session.execute(
            select(SiteDayAssignment).where(SiteDayAssignment.month_id == month.id)
        ).scalars()
    )
    assert assignments == []
