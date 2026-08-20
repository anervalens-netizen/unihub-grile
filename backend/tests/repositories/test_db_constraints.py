"""AC-02 DB constraint tests.

These tests run against the in-memory SQLite schema. The same DDL is
exercised against PostgreSQL by ``tests/integration/test_postgres_concurrent_ac02.py``
(marked ``postgres`` and skipped when no PG is configured); that test
proves the partial unique indexes reject concurrent inserts across two
threads, which the SQLite in-memory path cannot replicate.
"""

from __future__ import annotations

from datetime import date

import pytest

from ugrile.domain.enums import DayStatus, WorkingKind
from ugrile.domain.errors import CoverageInvariantError
from ugrile.repositories.assignments import AssignmentRepository
from ugrile.repositories.models import Month, SiteDayAssignment
from ugrile.repositories.months import MonthRepository


def _month(session, tenant_id: str) -> Month:
    m = MonthRepository(session).get_or_create(tenant_id, 2026, 8)
    session.commit()
    return m


def test_unique_constraint_blocks_two_agents_same_store_day(session, faker_tenant):
    month = _month(session, faker_tenant["tenant_id"])
    repo = AssignmentRepository(session)

    repo.upsert_working(
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["store_id"],
        person_id=faker_tenant["person_a_id"],
        business_date=date(2026, 8, 5),
        working_kind=WorkingKind.NORMAL,
    )
    session.commit()

    with pytest.raises(CoverageInvariantError) as ei:
        repo.upsert_working(
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["store_id"],
            person_id=faker_tenant["person_b_id"],
            business_date=date(2026, 8, 5),
            working_kind=WorkingKind.NORMAL,
        )
    assert ei.value.http_status == 409
    assert any(c["code"] == "MULTIPLE_AGENTS_PER_STORE_DAY" for c in ei.value.details["conflicts"])


def test_unique_constraint_blocks_one_agent_two_stores_same_day(session, faker_tenant):
    month = _month(session, faker_tenant["tenant_id"])
    repo = AssignmentRepository(session)

    repo.upsert_working(
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["store_id"],
        person_id=faker_tenant["person_a_id"],
        business_date=date(2026, 8, 6),
        working_kind=WorkingKind.EXTRA_OTHER,
    )
    session.commit()

    with pytest.raises(CoverageInvariantError) as ei:
        repo.upsert_working(
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["other_store_id"],
            person_id=faker_tenant["person_a_id"],
            business_date=date(2026, 8, 6),
            working_kind=WorkingKind.EXTRA_OTHER,
        )
    assert any(c["code"] == "MULTIPLE_STORES_PER_AGENT_DAY" for c in ei.value.details["conflicts"])


def test_off_rows_do_not_collide_on_store_day(session, faker_tenant):
    """OFF rows on the same store-day must not collide."""

    from ugrile.repositories.models import PersonDayAbsence

    month = _month(session, faker_tenant["tenant_id"])
    # Person A is OFF on day 7, person B is also OFF on day 7.
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


def test_remove_for_day_clears_working_row(session, faker_tenant):
    month = _month(session, faker_tenant["tenant_id"])
    repo = AssignmentRepository(session)
    repo.upsert_working(
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["store_id"],
        person_id=faker_tenant["person_a_id"],
        business_date=date(2026, 8, 8),
        working_kind=WorkingKind.NORMAL,
    )
    session.commit()
    deleted = repo.remove_for_day(
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["store_id"],
        person_id=faker_tenant["person_a_id"],
        business_date=date(2026, 8, 8),
    )
    session.commit()
    assert deleted == 1
    assert session.query(SiteDayAssignment).count() == 0
