"""Repository coverage tests at the assignment row level."""

from __future__ import annotations

from datetime import date

from ugrile.domain.enums import DayStatus, WorkingKind
from ugrile.repositories.assignments import AssignmentRepository
from ugrile.repositories.models import SiteDayAssignment
from ugrile.repositories.months import MonthRepository


def _open_month(session, tenant_id: str):
    return MonthRepository(session).get_or_create(tenant_id, 2026, 8)


def test_check_month_coverage_returns_empty_for_clean_data(session, faker_tenant):
    """A month that respects AC-02 at the DB layer has no coverage conflicts.

    The conflict-detection paths are exercised at the DB constraint level
    in ``test_db_constraints.py`` and at the domain level in
    ``tests/domain/test_coverage.py``. Here we just confirm the repository
    round-trip is empty when the partial unique indexes have done their job.
    """

    month = _open_month(session, faker_tenant["tenant_id"])
    repo = AssignmentRepository(session)
    repo.upsert_working(
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["store_id"],
        person_id=faker_tenant["person_a_id"],
        business_date=date(2026, 8, 1),
        working_kind=WorkingKind.NORMAL,
    )
    repo.upsert_working(
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["other_store_id"],
        person_id=faker_tenant["person_c_id"],
        business_date=date(2026, 8, 1),
        working_kind=WorkingKind.NORMAL,
    )
    session.commit()
    assert repo.check_month_coverage(month.id) == []
    repo.assert_month_coverage(month.id)  # no raise


def test_check_month_coverage_flags_manually_introduced_violation(session, faker_tenant):
    """Drop the partial unique indexes, inject a conflict, and assert the domain
    checker catches it.

    The partial unique indexes are the production defence; the domain check
    exists for diagnostics and as a pre-flight when migrations land before
    the indexes do.
    """

    from sqlalchemy import text

    month = _open_month(session, faker_tenant["tenant_id"])
    repo = AssignmentRepository(session)

    try:
        # Drop the partial unique indexes so we can introduce a violation on
        # purpose. The test restores them so subsequent tests are unaffected.
        session.execute(text("DROP INDEX IF EXISTS uq_site_day_one_working"))
        session.execute(text("DROP INDEX IF EXISTS uq_person_day_one_working"))
        session.commit()

        session.add_all(
            [
                SiteDayAssignment(
                    tenant_id=faker_tenant["tenant_id"],
                    month_id=month.id,
                    store_id=faker_tenant["store_id"],
                    person_id=faker_tenant["person_a_id"],
                    business_date=date(2026, 8, 2),
                    status=DayStatus.WORKING,
                    working_kind=WorkingKind.NORMAL.value,
                    revision=0,
                    source="TEST",
                ),
                SiteDayAssignment(
                    tenant_id=faker_tenant["tenant_id"],
                    month_id=month.id,
                    store_id=faker_tenant["store_id"],
                    person_id=faker_tenant["person_b_id"],
                    business_date=date(2026, 8, 2),
                    status=DayStatus.WORKING,
                    working_kind=WorkingKind.NORMAL.value,
                    revision=0,
                    source="TEST",
                ),
            ]
        )
        session.commit()

        conflicts = repo.check_month_coverage(month.id)
        codes = {c.code for c in conflicts}
        assert "MULTIPLE_AGENTS_PER_STORE_DAY" in codes
    finally:
        # Remove the conflicting rows so we can safely re-create the indexes.
        session.execute(
            text("DELETE FROM site_day_assignments WHERE business_date = :d AND source = 'TEST'"),
            {"d": date(2026, 8, 2)},
        )
        session.commit()
        # Restore the partial unique indexes for subsequent tests.
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


def test_assert_month_coverage_passes_on_clean_data(session, faker_tenant):
    month = _open_month(session, faker_tenant["tenant_id"])
    repo = AssignmentRepository(session)
    repo.upsert_working(
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["store_id"],
        person_id=faker_tenant["person_a_id"],
        business_date=date(2026, 8, 1),
        working_kind=WorkingKind.NORMAL,
    )
    repo.upsert_working(
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["other_store_id"],
        person_id=faker_tenant["person_c_id"],
        business_date=date(2026, 8, 1),
        working_kind=WorkingKind.NORMAL,
    )
    session.commit()
    repo.assert_month_coverage(month.id)  # no raise
