from __future__ import annotations

from datetime import date

import pytest

from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.domain.errors import ValidationError
from ugrile.repositories.models import Person, StoreAssignment
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService


def test_calendar_working_kind_uses_effective_dated_home_store(session, faker_tenant):
    """A later catalog transfer must not rewrite historical NORMAL semantics."""

    alice = session.get(Person, faker_tenant["person_a_id"])
    assert alice is not None
    alice.home_store_id = faker_tenant["other_store_id"]
    session.add(
        StoreAssignment(
            tenant_id=faker_tenant["tenant_id"],
            person_id=faker_tenant["person_a_id"],
            store_id=faker_tenant["store_id"],
            effective_from=date(2026, 8, 1),
            effective_to=date(2026, 8, 31),
        )
    )
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.commit()

    result = CalendarService(session).apply(
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
    session.commit()
    assert result.revision == 1

    with pytest.raises(ValidationError):
        CalendarService(session).apply(
            month=month,
            tenant_id=faker_tenant["tenant_id"],
            expected_revision=1,
            changes=[
                CalendarChange(
                    person_id=faker_tenant["person_a_id"],
                    business_date=date(2026, 8, 11),
                    store_id=faker_tenant["other_store_id"],
                    status=DayStatus.WORKING,
                    working_kind=WorkingKind.NORMAL,
                )
            ],
        )


def test_calendar_rejects_effective_home_history_gap(session, faker_tenant):
    """Known assignment history gaps fail closed instead of using current home."""

    session.add(
        StoreAssignment(
            tenant_id=faker_tenant["tenant_id"],
            person_id=faker_tenant["person_a_id"],
            store_id=faker_tenant["store_id"],
            effective_from=date(2026, 8, 1),
            effective_to=date(2026, 8, 9),
        )
    )
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.commit()

    with pytest.raises(ValidationError) as exc:
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
    assert "effective home store is unknown" in exc.value.message
