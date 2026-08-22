"""Repository coverage diagnostics over authoritative assignment rows."""

from __future__ import annotations

from datetime import date

from ugrile.domain.enums import DayStatus, WorkingKind
from ugrile.repositories.assignments import AssignmentRepository
from ugrile.repositories.models import SiteDayAssignment
from ugrile.repositories.months import MonthRepository


def _open_month(session, tenant_id: str):
    return MonthRepository(session).get_or_create(tenant_id, 2026, 8)


def _seed_working(
    session,
    *,
    tenant_id: str,
    month_id: str,
    store_id: str,
    person_id: str,
    business_date: date,
) -> None:
    session.add(
        SiteDayAssignment(
            tenant_id=tenant_id,
            month_id=month_id,
            store_id=store_id,
            person_id=person_id,
            business_date=business_date,
            status=DayStatus.WORKING,
            working_kind=WorkingKind.NORMAL.value,
            revision=0,
            source="TEST",
        )
    )


def test_coverage_diagnostics_pass_for_clean_persisted_data(session, faker_tenant):
    """Read-side diagnostics accept rows already protected by AC-02 indexes."""

    month = _open_month(session, faker_tenant["tenant_id"])
    _seed_working(
        session,
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["store_id"],
        person_id=faker_tenant["person_a_id"],
        business_date=date(2026, 8, 1),
    )
    _seed_working(
        session,
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["other_store_id"],
        person_id=faker_tenant["person_c_id"],
        business_date=date(2026, 8, 1),
    )
    session.commit()

    repo = AssignmentRepository(session)
    assert repo.check_month_coverage(month.id) == []
    repo.assert_month_coverage(month.id)


def test_check_month_coverage_flags_manually_introduced_violation(session, faker_tenant):
    """Domain diagnostics still detect corrupted state if DB guards are bypassed."""

    from sqlalchemy import text

    month = _open_month(session, faker_tenant["tenant_id"])
    repo = AssignmentRepository(session)

    try:
        session.execute(text("DROP INDEX IF EXISTS uq_site_day_one_working"))
        session.execute(text("DROP INDEX IF EXISTS uq_person_day_one_working"))
        session.commit()

        _seed_working(
            session,
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["store_id"],
            person_id=faker_tenant["person_a_id"],
            business_date=date(2026, 8, 2),
        )
        _seed_working(
            session,
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["store_id"],
            person_id=faker_tenant["person_b_id"],
            business_date=date(2026, 8, 2),
        )
        session.commit()

        conflicts = repo.check_month_coverage(month.id)
        assert "MULTIPLE_AGENTS_PER_STORE_DAY" in {conflict.code for conflict in conflicts}
    finally:
        session.execute(
            text("DELETE FROM site_day_assignments WHERE business_date = :d AND source = 'TEST'"),
            {"d": date(2026, 8, 2)},
        )
        session.commit()
        session.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_site_day_one_working "
                "ON site_day_assignments (tenant_id, store_id, business_date) "
                "WHERE status = 'WORKING'"
            )
        )
        session.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_person_day_one_working "
                "ON site_day_assignments (tenant_id, person_id, business_date) "
                "WHERE status = 'WORKING'"
            )
        )
        session.commit()
