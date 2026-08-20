"""S3 domain tests — month close blockers, lattice and reopen (AC-15).

The validator is evaluated over the authoritative open-store/day lattice:
every active store x every date of the month. A missing assignment row, a
missing physical sale, or a missing/zero target for an open day is an
explicit typed blocker — calculation completeness is never silently
bypassed.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ugrile.domain.close import (
    BlockerDetail,
    CloseBlockerCode,
    CloseValidation,
    MonthCloseEventRecord,
    OpenStoreDay,
    PersonDaySnapshot,
    ReopenValidation,
    SalesAvailabilitySnapshot,
    StoreCoverageSnapshot,
    StoreTargetAvailabilitySnapshot,
    assert_close_state,
    assert_reopen_state,
    month_close_event_digest,
    validate_close,
    verify_month_close_chain,
)
from ugrile.domain.enums import MonthState
from ugrile.domain.errors import MonthStateError

D1 = date(2026, 8, 1)
D2 = date(2026, 8, 2)


def _open(store_id: str, business_date: date) -> OpenStoreDay:
    return OpenStoreDay(store_id=store_id, business_date=business_date)


def _coverage(
    store_id: str,
    business_date: date,
    person_id: str | None,
    working_kind: str | None = "NORMAL",
    person_home_store_id: str | None = None,
) -> StoreCoverageSnapshot:
    return StoreCoverageSnapshot(
        store_id=store_id,
        business_date=business_date,
        person_id=person_id,
        working_kind=working_kind,
        person_home_store_id=person_home_store_id,
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


def _validate(
    *,
    open_days: list[OpenStoreDay],
    coverage: list[StoreCoverageSnapshot],
    person_days: list[PersonDaySnapshot] | None = None,
    sales: list[SalesAvailabilitySnapshot] | None = None,
    targets: list[StoreTargetAvailabilitySnapshot] | None = None,
    extra_blockers: tuple[CloseBlockerCode, ...] = (),
) -> CloseValidation:
    return validate_close(
        open_days=open_days,
        coverage=coverage,
        person_days=person_days or [],
        sales_availability=sales or [],
        target_availability=targets or [],
        extra_blockers=extra_blockers,
    )


def _covered_day(store_id: str, business_date: date, person_id: str) -> list[StoreCoverageSnapshot]:
    return [_coverage(store_id, business_date, person_id, "NORMAL", store_id)]


def test_open_day_without_any_assignment_is_uncovered():
    """An active store/day with no assignment row is a blocker (finding 1)."""

    result = _validate(
        open_days=[_open("s1", D1)],
        coverage=[],
    )
    assert not result.ok
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.STORE_DAY_UNCOVERED in codes
    uncovered = [b for b in result.blockers if b.code == CloseBlockerCode.STORE_DAY_UNCOVERED]
    assert uncovered[0].store_id == "s1"
    assert uncovered[0].business_date == D1


def test_off_row_does_not_occupy_store_coverage():
    """An OFF/LEAVE row never covers an open day (finding 1)."""

    result = _validate(
        open_days=[_open("s1", D1)],
        coverage=[_coverage("s1", D1, None, None)],  # OFF-style row
    )
    assert not result.ok
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.STORE_DAY_UNCOVERED in codes


def test_open_day_with_working_row_is_covered():
    result = _validate(
        open_days=[_open("s1", D1)],
        coverage=_covered_day("s1", D1, "p1"),
        person_days=[_person_day("p1", D1, "s1")],
        sales=[_sale("s1", D1, True)],
        targets=[_target("s1", D1, Decimal("10000"))],
    )
    assert result.ok, result.blockers


def test_lattice_day_outside_month_is_not_checked():
    """Only the explicit lattice matters: a date not listed is not open."""
    result = _validate(
        open_days=[_open("s1", D1)],
        coverage=_covered_day("s1", D1, "p1"),
        person_days=[_person_day("p1", D1, "s1")],
        sales=[_sale("s1", D1, True)],
        targets=[_target("s1", D1, Decimal("10000"))],
    )
    assert result.ok


def test_close_blocks_on_multiple_working_per_store_day():
    coverage = [
        _coverage("s1", D1, "p1"),
        _coverage("s1", D1, "p2"),
    ]
    result = _validate(
        open_days=[_open("s1", D1)],
        coverage=coverage,
        sales=[_sale("s1", D1, True)],
        targets=[_target("s1", D1, Decimal("10000"))],
    )
    assert not result.ok
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.STORE_DAY_MULTIPLE_WORKING in codes


def test_close_blocks_on_person_in_two_stores_same_day():
    coverage = [
        _coverage("s1", D1, "p1"),
        _coverage("s2", D1, "p1"),
    ]
    person_days = [
        _person_day("p1", D1, "s1"),
        _person_day("p1", D1, "s2"),
    ]
    sales = [
        _sale("s1", D1, True),
        _sale("s2", D1, True),
    ]
    targets = [
        _target("s1", D1, Decimal("10000")),
        _target("s2", D1, Decimal("5000")),
    ]
    result = _validate(
        open_days=[_open("s1", D1), _open("s2", D1)],
        coverage=coverage,
        person_days=person_days,
        sales=sales,
        targets=targets,
    )
    assert not result.ok
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.PERSON_DAY_MULTIPLE_WORKING in codes


def test_close_blocks_on_invalid_working_kind():
    """EXTRA_OTHER on the person's home store is INVALID_WORKING_KIND."""
    coverage = [_coverage("s1", D1, "p1", "EXTRA_OTHER", person_home_store_id="s1")]
    result = _validate(
        open_days=[_open("s1", D1)],
        coverage=coverage,
        sales=[_sale("s1", D1, True)],
        targets=[_target("s1", D1, Decimal("10000"))],
    )
    assert not result.ok
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.INVALID_WORKING_KIND in codes


def test_close_accepts_valid_extra_other():
    """EXTRA_OTHER on a different store is valid."""
    coverage = [_coverage("s2", D1, "p1", "EXTRA_OTHER", person_home_store_id="s1")]
    result = _validate(
        open_days=[_open("s2", D1)],
        coverage=coverage,
        person_days=[_person_day("p1", D1, "s2", "EXTRA_OTHER")],
        sales=[_sale("s2", D1, True)],
        targets=[_target("s2", D1, Decimal("10000"))],
    )
    assert result.ok, result.blockers


def test_close_blocks_on_missing_sale_over_full_lattice():
    """An open day with no SalesStoreDay is a full-lattice blocker."""
    result = _validate(
        open_days=[_open("s1", D1)],
        coverage=_covered_day("s1", D1, "p1"),
        person_days=[_person_day("p1", D1, "s1")],
        sales=[],  # no sale
        targets=[_target("s1", D1, Decimal("10000"))],
    )
    assert not result.ok
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.SALES_MISSING_FOR_WORKED_DAY in codes


def test_close_blocks_on_orphan_sale():
    sales = [_sale("s1", D1, True)]  # sale exists
    result = _validate(
        open_days=[_open("s1", D1)],
        coverage=[],
        sales=sales,
    )
    assert not result.ok
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.SALES_ORPHAN_FOR_COVERED_DAY in codes


def test_close_blocks_on_zero_target_over_full_lattice():
    result = _validate(
        open_days=[_open("s1", D1)],
        coverage=_covered_day("s1", D1, "p1"),
        person_days=[_person_day("p1", D1, "s1")],
        sales=[_sale("s1", D1, True)],
        targets=[_target("s1", D1, Decimal("0"))],
    )
    assert not result.ok
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.TARGET_ZERO_FOR_WORKED_STORE in codes


def test_uncovered_day_reports_all_lattice_blockers_precisely():
    """An uncovered open day fires uncovered + missing sale + missing target."""
    result = _validate(
        open_days=[_open("s1", D1)],
        coverage=[],
        sales=[],
        targets=[],
    )
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.STORE_DAY_UNCOVERED in codes
    assert CloseBlockerCode.SALES_MISSING_FOR_WORKED_DAY in codes
    assert CloseBlockerCode.TARGET_ZERO_FOR_WORKED_STORE in codes


def test_deferred_blockers_surfaced_verbatim():
    coverage = _covered_day("s1", D1, "p1")
    result = _validate(
        open_days=[_open("s1", D1)],
        coverage=coverage,
        sales=[_sale("s1", D1, True)],
        targets=[_target("s1", D1, Decimal("10000"))],
        extra_blockers=(
            CloseBlockerCode.SHEET_CANARY_REQUIRED,
            CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED,
        ),
    )
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.SHEET_CANARY_REQUIRED in codes
    assert CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED in codes


def test_close_blocks_are_sorted_deterministically():
    coverage = [
        _coverage("s1", D1, "p1"),
        _coverage("s2", D2, "p1"),
    ]
    result = _validate(
        open_days=[_open("s1", D1), _open("s2", D2)],
        coverage=coverage,
    )
    keys = [(b.code.value, b.store_id or "", b.business_date or date.min) for b in result.blockers]
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
    assert (
        CloseValidation(
            blockers=(
                BlockerDetail(
                    code=CloseBlockerCode.STORE_DAY_MULTIPLE_WORKING,
                    store_id="s1",
                    person_id=None,
                    business_date=D1,
                    message="",
                ),
            )
        ).ok
        is False
    )


def _record(
    *,
    event_id: int,
    previous_event_digest: str | None,
    event_digest: str | None = None,
    action: str = "CLOSE",
    previous_state: str = "OPEN",
    new_state: str = "CLOSED",
    revision_before: int = 1,
    revision_after: int = 2,
    reason: str | None = None,
) -> MonthCloseEventRecord:
    if event_digest is None:
        event_digest = month_close_event_digest(
            tenant_id="t1",
            month_id="m1",
            action=action,
            previous_state=previous_state,
            new_state=new_state,
            revision_before=revision_before,
            revision_after=revision_after,
            actor_id="admin",
            reason=reason,
            blockers="[]",
            previous_event_digest=previous_event_digest,
        )
    return MonthCloseEventRecord(
        id=event_id,
        tenant_id="t1",
        month_id="m1",
        action=action,
        previous_state=previous_state,
        new_state=new_state,
        revision_before=revision_before,
        revision_after=revision_after,
        actor_id="admin",
        reason=reason,
        blockers="[]",
        previous_event_digest=previous_event_digest,
        event_digest=event_digest,
    )


def test_month_close_event_digest_is_deterministic():
    kwargs = dict(
        tenant_id="t1",
        month_id="m1",
        action="CLOSE",
        previous_state="OPEN",
        new_state="CLOSED",
        revision_before=1,
        revision_after=2,
        actor_id="admin",
        reason=None,
        blockers="[]",
        previous_event_digest=None,
    )
    first = month_close_event_digest(**kwargs)
    second = month_close_event_digest(**kwargs)
    assert first == second
    assert len(first) == 64
    # Any field change changes the digest.
    changed = month_close_event_digest(**{**kwargs, "revision_after": 3})
    assert changed != first
    chained = month_close_event_digest(**{**kwargs, "previous_event_digest": first})
    assert chained != first


def test_verify_month_close_chain_accepts_intact_chain():
    first = _record(event_id=1, previous_event_digest=None)
    second = _record(
        event_id=2,
        previous_event_digest=first.event_digest,
        action="REOPEN",
        previous_state="CLOSED",
        new_state="REOPENED",
        revision_before=2,
        revision_after=3,
        reason="corectie",
    )
    assert verify_month_close_chain([first, second]) == []


def test_verify_month_close_chain_detects_tampering():
    first = _record(event_id=1, previous_event_digest=None)
    _record(
        event_id=2,
        previous_event_digest=first.event_digest,
        action="REOPEN",
        previous_state="CLOSED",
        new_state="REOPENED",
        revision_before=2,
        revision_after=3,
        reason="corectie",
    )
    # Tamper: the second record claims a different previous digest.
    tampered = _record(
        event_id=2,
        previous_event_digest="deadbeef",
        action="REOPEN",
        previous_state="CLOSED",
        new_state="REOPENED",
        revision_before=2,
        revision_after=3,
        reason="corectie",
    )
    issues = verify_month_close_chain([first, tampered])
    assert any("previous_event_digest mismatch" in issue for issue in issues)
    # Tamper: the first record's own digest no longer matches its fields.
    bad_first = _record(event_id=1, previous_event_digest=None, event_digest="tampered")
    issues = verify_month_close_chain([bad_first])
    assert any("event_digest mismatch" in issue for issue in issues)


@pytest.mark.parametrize(
    "open_days, coverage, person_days, sales, targets, expected_codes",
    [
        # Happy path: one WORKING row, sale present, target positive.
        (
            [_open("s1", D1)],
            _covered_day("s1", D1, "p1"),
            [_person_day("p1", D1, "s1")],
            [_sale("s1", D1, True)],
            [_target("s1", D1, Decimal("10000"))],
            set(),
        ),
        # Missing sale only.
        (
            [_open("s1", D1)],
            _covered_day("s1", D1, "p1"),
            [_person_day("p1", D1, "s1")],
            [],
            [_target("s1", D1, Decimal("10000"))],
            {CloseBlockerCode.SALES_MISSING_FOR_WORKED_DAY},
        ),
        # Zero target only.
        (
            [_open("s1", D1)],
            _covered_day("s1", D1, "p1"),
            [_person_day("p1", D1, "s1")],
            [_sale("s1", D1, True)],
            [_target("s1", D1, Decimal("0"))],
            {CloseBlockerCode.TARGET_ZERO_FOR_WORKED_STORE},
        ),
        # No assignment row -> uncovered (with no sale/target the day also
        # reports the full-lattice missing sale/target blockers precisely).
        (
            [_open("s1", D1)],
            [],
            [],
            [],
            [],
            {
                CloseBlockerCode.STORE_DAY_UNCOVERED,
                CloseBlockerCode.SALES_MISSING_FOR_WORKED_DAY,
                CloseBlockerCode.TARGET_ZERO_FOR_WORKED_STORE,
            },
        ),
    ],
)
def test_table_driven_close(open_days, coverage, person_days, sales, targets, expected_codes):
    result = _validate(
        open_days=open_days,
        coverage=coverage,
        person_days=person_days,
        sales=sales,
        targets=targets,
    )
    assert {b.code for b in result.blockers} == expected_codes
