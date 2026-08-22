"""AC-02 DB constraint tests.

These tests run against the in-memory SQLite schema. The same DDL is
exercised against PostgreSQL by ``tests/integration/test_postgres_concurrent_ac02.py``
(marked ``postgres`` and skipped when no PG is configured); that test proves
the partial unique indexes reject concurrent inserts across two threads,
which the SQLite in-memory path cannot replicate.

The tests insert ORM rows directly: calendar business mutation belongs to
``CalendarService`` and the repository no longer exposes a parallel write path.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from ugrile.domain.enums import DayStatus, WorkingKind
from ugrile.repositories.models import Month, PersonDayAbsence, SiteDayAssignment
from ugrile.repositories.months import MonthRepository


def _month(session, tenant_id: str) -> Month:
    m = MonthRepository(session).get_or_create(tenant_id, 2026, 8)
    session.commit()
    return m


def _working_row(
    *,
    tenant_id: str,
    month_id: str,
    store_id: str,
    person_id: str,
    business_date: date,
    working_kind: WorkingKind = WorkingKind.NORMAL,
) -> SiteDayAssignment:
    return SiteDayAssignment(
        tenant_id=tenant_id,
        month_id=month_id,
        store_id=store_id,
        person_id=person_id,
        business_date=business_date,
        status=DayStatus.WORKING.value,
        working_kind=working_kind.value,
        revision=0,
        source="TEST",
    )


def test_unique_constraint_blocks_two_agents_same_store_day(session, faker_tenant):
    month = _month(session, faker_tenant["tenant_id"])
    session.add(
        _working_row(
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["store_id"],
            person_id=faker_tenant["person_a_id"],
            business_date=date(2026, 8, 5),
        )
    )
    session.commit()

    session.add(
        _working_row(
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["store_id"],
            person_id=faker_tenant["person_b_id"],
            business_date=date(2026, 8, 5),
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_unique_constraint_blocks_one_agent_two_stores_same_day(session, faker_tenant):
    month = _month(session, faker_tenant["tenant_id"])
    session.add(
        _working_row(
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["store_id"],
            person_id=faker_tenant["person_a_id"],
            business_date=date(2026, 8, 6),
            working_kind=WorkingKind.EXTRA_OTHER,
        )
    )
    session.commit()

    session.add(
        _working_row(
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["other_store_id"],
            person_id=faker_tenant["person_a_id"],
            business_date=date(2026, 8, 6),
            working_kind=WorkingKind.EXTRA_OTHER,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_off_rows_do_not_collide_on_store_day(session, faker_tenant):
    """OFF rows on the same business date do not occupy store coverage."""

    month = _month(session, faker_tenant["tenant_id"])
    session.add_all(
        [
            PersonDayAbsence(
                tenant_id=faker_tenant["tenant_id"],
                month_id=month.id,
                person_id=faker_tenant["person_a_id"],
                business_date=date(2026, 8, 7),
                status=DayStatus.OFF,
            ),
            PersonDayAbsence(
                tenant_id=faker_tenant["tenant_id"],
                month_id=month.id,
                person_id=faker_tenant["person_b_id"],
                business_date=date(2026, 8, 7),
                status=DayStatus.OFF,
            ),
        ]
    )
    session.commit()
    rows = session.query(PersonDayAbsence).all()
    assert len(rows) == 2
