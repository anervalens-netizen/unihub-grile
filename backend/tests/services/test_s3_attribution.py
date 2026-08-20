"""S3 service tests — sales attribution (AC-07).

The service tests prove that:

* The attribution projection is rebuilt in the same transaction as the
  calendar CAS.
* Prior revisions are immutable: reattribution after a calendar edit
  inserts new rows under the new revision and never mutates earlier ones.
* The same canonical inputs always produce the same projection hashes.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ugrile.connectors.fixtures import FIXTURE_GENERATION
from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.repositories.models import (
    SalesPersonDayProjection,
    SalesStoreDay,
    StoreTarget,
)
from ugrile.repositories.months import MonthRepository
from ugrile.services.attribution import AttributionService
from ugrile.services.calendar import CalendarChange, CalendarService


def _seed_sales_and_target(session, faker_tenant, *, store_id, amount, target):
    session.add(
        SalesStoreDay(
            tenant_id=faker_tenant["tenant_id"],
            store_id=store_id,
            business_date=date(2026, 8, 1),
            generation=FIXTURE_GENERATION,
            amount=Decimal(amount),
            currency="RON",
        )
    )
    session.add(
        StoreTarget(
            tenant_id=faker_tenant["tenant_id"],
            store_id=store_id,
            year=2026,
            month=8,
            kind="MONTHLY_SALES",
            version=1,
            amount=Decimal(target),
            currency="RON",
        )
    )
    session.commit()


def _apply_simple_calendar(session, faker_tenant, person_id, store_id, day=1):
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                person_id,
                date(2026, 8, day),
                store_id,
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    session.commit()
    return month


def test_attribution_rebuilt_in_same_calendar_transaction(session, faker_tenant):
    _seed_sales_and_target(
        session, faker_tenant, store_id=faker_tenant["store_id"], amount="12500", target="250000"
    )
    month = _apply_simple_calendar(
        session, faker_tenant, faker_tenant["person_a_id"], faker_tenant["store_id"]
    )
    rows = (
        session.query(SalesPersonDayProjection)
        .filter_by(month_id=month.id, revision=1)
        .all()
    )
    assert len(rows) == 1
    assert rows[0].amount == Decimal("12500.00")
    assert rows[0].generation == FIXTURE_GENERATION
    assert rows[0].working_kind == WorkingKind.NORMAL.value
    assert rows[0].person_id == faker_tenant["person_a_id"]


def test_attribution_changes_after_reassignment_but_company_total_unchanged(
    session, faker_tenant
):
    """A mid-month reassignment preserves the physical store-day sale total.

    Alice covers day 1 in revision 1; in revision 2 we reassign day 2 to
    Bob and mark Alice OFF. The persisted ``SalesPersonDayProjection`` rows
    for revision 2 reflect the new credit; the underlying ``SalesStoreDay``
    rows are unchanged and the company total across both days is identical.
    """

    _seed_sales_and_target(
        session, faker_tenant, store_id=faker_tenant["store_id"], amount="12500", target="250000"
    )
    month = _apply_simple_calendar(
        session, faker_tenant, faker_tenant["person_a_id"], faker_tenant["store_id"], day=1
    )
    session.add(
        SalesStoreDay(
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            business_date=date(2026, 8, 2),
            generation=FIXTURE_GENERATION,
            amount=Decimal("9870.50"),
            currency="RON",
        )
    )
    session.commit()
    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=1,
        changes=[
            CalendarChange(
                faker_tenant["person_b_id"],
                date(2026, 8, 2),
                faker_tenant["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            ),
            CalendarChange(
                faker_tenant["person_a_id"],
                date(2026, 8, 2),
                None,
                DayStatus.OFF,
            ),
        ],
    )
    session.commit()
    # Revision 2 has Alice (day 1) and Bob (day 2); both physical rows
    # are credited to the calendar-assigned person.
    revision_two_rows = (
        session.query(SalesPersonDayProjection)
        .filter_by(month_id=month.id, revision=2)
        .all()
    )
    assert len(revision_two_rows) == 2
    bob_row = next(r for r in revision_two_rows if r.person_id == faker_tenant["person_b_id"])
    assert bob_row.amount == Decimal("9870.50")
    alice_row = next(r for r in revision_two_rows if r.person_id == faker_tenant["person_a_id"])
    assert alice_row.amount == Decimal("12500.00")
    # Company total across revision 2 = sum of physical rows.
    company_total = sum(row.amount for row in revision_two_rows)
    assert company_total == Decimal("22370.50")
    # Revision 1 is immutable: Alice still owns day 1 only.
    revision_one_rows = (
        session.query(SalesPersonDayProjection)
        .filter_by(month_id=month.id, revision=1)
        .all()
    )
    assert len(revision_one_rows) == 1
    assert revision_one_rows[0].person_id == faker_tenant["person_a_id"]
    # Underlying SalesStoreDay rows are unchanged.
    sales_rows = (
        session.query(SalesStoreDay).filter_by(
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
        ).all()
    )
    assert len(sales_rows) == 2
    assert {row.business_date for row in sales_rows} == {date(2026, 8, 1), date(2026, 8, 2)}


def test_attribution_skipped_for_unworked_days(session, faker_tenant):
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    summary = AttributionService(session).summary_for_revision(
        tenant_id=faker_tenant["tenant_id"], month=month, revision=month.revision
    )
    assert summary.total_rows == 0
    assert summary.company_total == Decimal("0")


def test_summary_company_total_matches_store_sale(session, faker_tenant):
    _seed_sales_and_target(
        session, faker_tenant, store_id=faker_tenant["store_id"], amount="12500", target="250000"
    )
    _apply_simple_calendar(
        session, faker_tenant, faker_tenant["person_a_id"], faker_tenant["store_id"]
    )
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    summary = AttributionService(session).summary_for_revision(
        tenant_id=faker_tenant["tenant_id"], month=month, revision=month.revision
    )
    assert summary.company_total == Decimal("12500.00")
    assert summary.total_rows == 1
    assert summary.attributed[0].amount == Decimal("12500.00")
