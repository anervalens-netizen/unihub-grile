"""S3 domain tests — month close blockers and reopen validation (AC-15)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ugrile.domain.close import (
    BlockerDetail,
    CloseBlockerCode,
    CloseValidation,
    PersonDaySnapshot,
    ReopenValidation,
    SalesAvailabilitySnapshot,
    StoreCoverageSnapshot,
    StoreTargetAvailabilitySnapshot,
    assert_close_state,
    assert_reopen_state,
    validate_close,
)
from ugrile.domain.enums import MonthState
from ugrile.domain.errors import MonthStateError


def _coverage(
    store_id: str, business_date: date, person_id: str | None, working_kind: str | None = "NORMAL"
) -> StoreCoverageSnapshot:
    return StoreCoverageSnapshot(
        store_id=store_id,
        business_date=business_date,
        person_id=person_id,
        working_kind=working_kind,
    )


def _person_day(
    person_id: str, business_date: date, store_id: str, working_kind: str | None = "NORMAL"
) -> PersonDaySnapshot:
    return PersonDaySnapshot(
        person_id=person_id,
        business_date=business_date,
        store_id=store_id,
        working_kind=working_kind,
    )


def _sale(store_id: str, business_date: date, present: bool) -> SalesAvailabilitySnapshot:
    return SalesAvailabilitySnapshot(
        store_id=store_id,
        business_date=business_date,
        has_sale=present,
    )


def _target(
    store_id: str, business_date: date, amount: Decimal, present: bool = True
) -> StoreTargetAvailabilitySnapshot:
    return StoreTargetAvailabilitySnapshot(
        store_id=store_id,
        business_date=business_date,
        has_target=present,
        target_amount=amount,
    )


def test_close_blocks_on_uncovered_day():
    coverage = [
        _coverage("s1", date(2026, 8, 1), None, None),  # OFF-style row
    ]
    sales: list[SalesAvailabilitySnapshot] = []
    targets = [_target("s1", date(2026, 8, 1), Decimal("10000"))]
    result = validate_close(
        coverage=coverage, person_days=[], sales_availability=sales, target_availability=targets
    )
    # An OFF row is fine on its own; only WORKING rows contribute to blockers.
    assert result.ok


def test_close_blocks_on_multiple_working_per_store_day():
    coverage = [
        _coverage("s1", date(2026, 8, 1), "p1"),
        _coverage("s1", date(2026, 8, 1), "p2"),
    ]
    sales = [_sale("s1", date(2026, 8, 1), True)]
    targets = [_target("s1", date(2026, 8, 1), Decimal("10000"))]
    result = validate_close(
        coverage=coverage, person_days=[], sales_availability=sales, target_availability=targets
    )
    assert not result.ok
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.STORE_DAY_MULTIPLE_WORKING in codes


def test_close_blocks_on_person_in_two_stores_same_day():
    coverage = [
        _coverage("s1", date(2026, 8, 1), "p1"),
        _coverage("s2", date(2026, 8, 1), "p1"),
    ]
    person_days = [
        _person_day("p1", date(2026, 8, 1), "s1"),
        _person_day("p1", date(2026, 8, 1), "s2"),
    ]
    sales = [
        _sale("s1", date(2026, 8, 1), True),
        _sale("s2", date(2026, 8, 1), True),
    ]
    targets = [
        _target("s1", date(2026, 8, 1), Decimal("10000")),
        _target("s2", date(2026, 8, 1), Decimal("5000")),
    ]
    result = validate_close(
        coverage=coverage,
        person_days=person_days,
        sales_availability=sales,
        target_availability=targets,
    )
    assert not result.ok
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.PERSON_DAY_MULTIPLE_WORKING in codes


def test_close_blocks_on_missing_sale_for_worked_day():
    coverage = [_coverage("s1", date(2026, 8, 1), "p1")]
    sales: list[SalesAvailabilitySnapshot] = []  # no sale
    targets = [_target("s1", date(2026, 8, 1), Decimal("10000"))]
    result = validate_close(
        coverage=coverage, person_days=[], sales_availability=sales, target_availability=targets
    )
    assert not result.ok
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.SALES_MISSING_FOR_WORKED_DAY in codes


def test_close_blocks_on_orphan_sale():
    sales = [_sale("s1", date(2026, 8, 1), True)]  # sale exists
    targets: list[StoreTargetAvailabilitySnapshot] = []
    result = validate_close(
        coverage=[],
        person_days=[],
        sales_availability=sales,
        target_availability=targets,
    )
    assert not result.ok
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.SALES_ORPHAN_FOR_COVERED_DAY in codes


def test_close_blocks_on_zero_target_for_worked_store_day():
    coverage = [_coverage("s1", date(2026, 8, 1), "p1")]
    sales = [_sale("s1", date(2026, 8, 1), True)]
    targets = [_target("s1", date(2026, 8, 1), Decimal("0"))]
    result = validate_close(
        coverage=coverage, person_days=[], sales_availability=sales, target_availability=targets
    )
    assert not result.ok
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.TARGET_ZERO_FOR_WORKED_STORE in codes


def test_deferred_blockers_surfaced_verbatim():
    coverage = [_coverage("s1", date(2026, 8, 1), "p1")]
    sales = [_sale("s1", date(2026, 8, 1), True)]
    targets = [_target("s1", date(2026, 8, 1), Decimal("10000"))]
    result = validate_close(
        coverage=coverage,
        person_days=[],
        sales_availability=sales,
        target_availability=targets,
        extra_blockers=[
            CloseBlockerCode.SHEET_CANARY_REQUIRED,
            CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED,
        ],
    )
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.SHEET_CANARY_REQUIRED in codes
    assert CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED in codes


def test_close_blocks_are_sorted_deterministically():
    coverage = [
        _coverage("s1", date(2026, 8, 1), "p1"),
        _coverage("s2", date(2026, 8, 2), "p1"),
    ]
    sales: list[SalesAvailabilitySnapshot] = []
    targets: list[StoreTargetAvailabilitySnapshot] = []
    result = validate_close(
        coverage=coverage,
        person_days=[],
        sales_availability=sales,
        target_availability=targets,
    )
    keys = [
        (b.code.value, b.store_id or "", b.business_date or date.min)
        for b in result.blockers
    ]
    assert keys == sorted(keys)


def test_assert_close_state_rejects_unopened():
    with pytest.raises(MonthStateError):
        assert_close_state(MonthState.CLOSED)
    with pytest.raises(MonthStateError):
        assert_close_state(MonthState.DRAFT)
    # OPEN and REOPENED are accepted.
    assert_close_state(MonthState.OPEN)
    assert_close_state(MonthState.REOPENED)


def test_assert_reopen_state_requires_closed():
    with pytest.raises(MonthStateError):
        assert_reopen_state(MonthState.OPEN)
    assert_reopen_state(MonthState.CLOSED)


def test_reopen_reason_validation():
    validator = ReopenValidation(reason_required=True)
    ok, _ = validator.validate_reason("correctare target")
    assert ok
    ok, error = validator.validate_reason("")
    assert not ok
    assert "required" in (error or "")
    ok, error = validator.validate_reason("  ")
    assert not ok
    ok, error = validator.validate_reason("ab")
    assert not ok
    assert "4 characters" in (error or "")
    ok, error = validator.validate_reason(None)
    assert not ok


def test_close_validation_ok_property():
    empty = CloseValidation(blockers=())
    assert empty.ok
    assert CloseValidation(blockers=(BlockerDetail(
        code=CloseBlockerCode.STORE_DAY_MULTIPLE_WORKING,
        store_id="s1",
        person_id=None,
        business_date=date(2026, 8, 1),
        message="",
    ),)).ok is False


@pytest.mark.parametrize(
    "coverage, person_days, sales, targets, expected_codes",
    [
        # Happy path: one WORKING row, sale present, target positive.
        (
            [_coverage("s1", date(2026, 8, 1), "p1")],
            [_person_day("p1", date(2026, 8, 1), "s1")],
            [_sale("s1", date(2026, 8, 1), True)],
            [_target("s1", date(2026, 8, 1), Decimal("10000"))],
            set(),
        ),
        # Missing sale only.
        (
            [_coverage("s1", date(2026, 8, 1), "p1")],
            [_person_day("p1", date(2026, 8, 1), "s1")],
            [],
            [_target("s1", date(2026, 8, 1), Decimal("10000"))],
            {CloseBlockerCode.SALES_MISSING_FOR_WORKED_DAY},
        ),
        # Zero target only.
        (
            [_coverage("s1", date(2026, 8, 1), "p1")],
            [_person_day("p1", date(2026, 8, 1), "s1")],
            [_sale("s1", date(2026, 8, 1), True)],
            [_target("s1", date(2026, 8, 1), Decimal("0"))],
            {CloseBlockerCode.TARGET_ZERO_FOR_WORKED_STORE},
        ),
    ],
)
def test_table_driven_close(
    coverage, person_days, sales, targets, expected_codes
):
    result = validate_close(
        coverage=coverage,
        person_days=person_days,
        sales_availability=sales,
        target_availability=targets,
    )
    assert {b.code for b in result.blockers} == expected_codes
