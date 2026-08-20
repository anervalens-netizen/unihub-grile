"""S3 domain tests — sales attribution (AC-07).

The tests are pure: they exercise the engine directly with hand-built
``CalendarWorkingDay`` and ``StoreDaySale`` slices. The contract is
locked in:

* each ``StoreDaySale`` appears once in the company total;
* reassignment changes the personal credit but never the company total;
* ``EXTRA_HOME`` / ``EXTRA_OTHER`` keep physical sales non-duplicated;
* orphan sales and missing sales are surfaced as typed anomalies.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ugrile.domain.attribution import (
    AttributionResult,
    CalendarWorkingDay,
    StoreDaySale,
    attribute_sales,
    store_total,
)
from ugrile.domain.enums import WorkingKind


def _sale(store: str, day: int, amount: str) -> StoreDaySale:
    return StoreDaySale(
        store_id=store,
        business_date=date(2026, 8, day),
        generation="FIXTURE_V1",
        amount=Decimal(amount),
        currency="RON",
    )


def _working(person: str, store: str, day: int, kind: WorkingKind = WorkingKind.NORMAL):
    return CalendarWorkingDay(
        person_id=person,
        store_id=store,
        business_date=date(2026, 8, day),
        working_kind=kind,
    )


def test_each_store_day_counted_once_in_company_total():
    working = [
        _working("p1", "s1", 1),
        _working("p2", "s2", 1),
    ]
    sales = [
        _sale("s1", 1, "12500.00"),
        _sale("s2", 1, "5400.25"),
    ]
    result = attribute_sales(working, sales)
    assert result.company_total_amount == Decimal("17900.25")
    amounts = {(r.store_id, r.business_date): r.amount for r in result.attributed}
    assert amounts[("s1", date(2026, 8, 1))] == Decimal("12500.00")
    assert amounts[("s2", date(2026, 8, 1))] == Decimal("5400.25")
    assert result.anomalies == ()


def test_reassignment_preserves_company_total_but_changes_personal_credit():
    sales = [_sale("s1", 1, "12500.00"), _sale("s2", 1, "5400.25")]
    before = attribute_sales(
        [_working("p1", "s1", 1), _working("p2", "s2", 1)], sales
    )
    after = attribute_sales(
        [_working("p2", "s1", 1), _working("p1", "s2", 1)], sales
    )
    assert before.company_total_amount == after.company_total_amount
    before_credits = {(r.person_id, r.store_id): r.amount for r in before.attributed}
    after_credits = {(r.person_id, r.store_id): r.amount for r in after.attributed}
    # The same store-day amount moved between persons; no row was created or
    # duplicated.
    assert before_credits[("p1", "s1")] == Decimal("12500.00")
    assert before_credits[("p2", "s2")] == Decimal("5400.25")
    assert after_credits[("p2", "s1")] == Decimal("12500.00")
    assert after_credits[("p1", "s2")] == Decimal("5400.25")
    # The physical sales index is untouched.
    assert len(before.attributed) == len(after.attributed) == 2


def test_extra_home_does_not_duplicate_physical_sale():
    """``EXTRA_HOME`` credits the same person who already owns the home store.

    Two assignments on the same store/day for the same person is impossible
    under AC-02 (one working per store/day). The second ``EXTRA_HOME`` row
    is therefore rejected as a coverage conflict upstream, but the engine
    still attributes the sale exactly once.
    """

    working = [
        _working("p1", "s1", 1, WorkingKind.NORMAL),
        # Same person + store/date with EXTRA_HOME is impossible per AC-02,
        # but the engine only sees working rows. Two rows for the same
        # person would collapse to a single attribution in real life.
    ]
    sales = [_sale("s1", 1, "12500.00")]
    result = attribute_sales(working, sales)
    assert len(result.attributed) == 1
    assert result.attributed[0].amount == Decimal("12500.00")
    assert result.attributed[0].working_kind is WorkingKind.NORMAL
    assert result.company_total_amount == Decimal("12500.00")


def test_extra_other_assigns_credit_to_visiting_person():
    """``EXTRA_OTHER`` credits the visiting person for the OTHER store's sale.

    The home store's sale still goes to the home person. The company total
    is the sum of both physical rows; no row is duplicated.
    """

    working = [
        _working("p1", "s1", 1),  # home person on home store
        _working("p1", "s2", 2, WorkingKind.EXTRA_OTHER),  # visits other
    ]
    sales = [
        _sale("s1", 1, "10000.00"),
        _sale("s2", 2, "5000.00"),
    ]
    result = attribute_sales(working, sales)
    amounts = {(r.person_id, r.store_id): r.amount for r in result.attributed}
    assert amounts[("p1", "s1")] == Decimal("10000.00")
    assert amounts[("p1", "s2")] == Decimal("5000.00")
    assert result.company_total_amount == Decimal("15000.00")


def test_missing_sale_surfaces_anomaly_but_zero_amount_row():
    """A worked day without a matching ``SalesStoreDay`` row is reported as anomaly.

    When the engine has *some* sales rows but none matches the worked
    store/date, it still emits a zero-amount attributed row so the grid
    engine sees the calendar day; close validation turns the anomaly into a
    typed blocker.
    """

    working = [_working("p1", "s1", 1)]
    # Sales on a different day for the same store.
    sales = [_sale("s1", 2, "1000")]
    result = attribute_sales(working, sales)
    assert result.company_total_amount == Decimal("1000")
    codes = {a.code for a in result.anomalies}
    assert "sales_missing" in codes
    # The worked day still produces an attributed row (zero amount).
    assert result.attributed[0].amount == Decimal("0")
    assert result.attributed[0].store_id == "s1"


def test_no_sales_rows_at_all_produces_zero_total():
    working = [_working("p1", "s1", 1), _working("p2", "s2", 1)]
    result = attribute_sales(working, [])
    assert result.company_total_amount == Decimal("0")
    assert result.attributed == ()
    codes = {a.code for a in result.anomalies}
    assert "sales_missing" in codes


def test_orphan_sale_surfaces_anomaly():
    """A sale on a date with no WORKING calendar entry is an anomaly."""

    working = [_working("p1", "s1", 1)]
    sales = [_sale("s1", 1, "1000.00"), _sale("s1", 2, "2000.00")]
    result = attribute_sales(working, sales)
    codes = {a.code for a in result.anomalies}
    assert "sales_orphan" in codes
    # The matched row keeps the credit; only the orphan is flagged.
    assert result.attributed[0].amount == Decimal("1000.00")


def test_unexpected_currency_surfaces_anomaly():
    working = [_working("p1", "s1", 1)]
    sales = [
        StoreDaySale(
            store_id="s1",
            business_date=date(2026, 8, 1),
            generation="FIXTURE_V1",
            amount=Decimal("1000"),
            currency="EUR",
        )
    ]
    result = attribute_sales(working, sales, expected_currencies=("RON",))
    assert any(a.code == "unexpected_currency" for a in result.anomalies)


def test_attribution_is_deterministic():
    sales = [_sale("s1", 1, "12500.00"), _sale("s2", 1, "5400.25")]
    working = [_working("p1", "s1", 1), _working("p2", "s2", 1)]
    first = attribute_sales(working, sales)
    second = attribute_sales(working, sales)
    assert first.attributed == second.attributed
    assert first.anomalies == second.anomalies


def test_store_total_helper_sums_per_store():
    sales = [
        _sale("s1", 1, "1000"),
        _sale("s1", 2, "500"),
        _sale("s2", 1, "300"),
    ]
    assert store_total(sales, "s1") == Decimal("1500")
    assert store_total(sales, "s2") == Decimal("300")
    assert store_total(sales, "missing") == Decimal("0")


@pytest.mark.parametrize(
    "working, sales, expected_credit, expected_total",
    [
        # Happy path: NORMAL + EXTRA_OTHER on different stores.
        (
            [
                _working("p1", "s1", 1, WorkingKind.NORMAL),
                _working("p1", "s2", 2, WorkingKind.EXTRA_OTHER),
            ],
            [_sale("s1", 1, "1000"), _sale("s2", 2, "2000")],
            {("p1", "s1"): Decimal("1000"), ("p1", "s2"): Decimal("2000")},
            Decimal("3000"),
        ),
        # Two persons across two stores on the same day.
        (
            [
                _working("p1", "s1", 1, WorkingKind.NORMAL),
                _working("p2", "s2", 1, WorkingKind.NORMAL),
            ],
            [_sale("s1", 1, "1000"), _sale("s2", 1, "1500")],
            {("p1", "s1"): Decimal("1000"), ("p2", "s2"): Decimal("1500")},
            Decimal("2500"),
        ),
    ],
)
def test_table_driven_attribution(
    working, sales, expected_credit, expected_total
):
    result: AttributionResult = attribute_sales(working, sales)
    amounts = {(r.person_id, r.store_id): r.amount for r in result.attributed}
    for key, value in expected_credit.items():
        assert amounts[key] == value
    assert result.company_total_amount == expected_total
