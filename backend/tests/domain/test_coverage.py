"""Domain-level AC-02 tests.

These tests are pure: no DB, no fixtures beyond a tiny dataclass. They prove
that the calendar invariants are checkable in isolation so other layers can
delegate to them.
"""

from __future__ import annotations

from datetime import date

import pytest

from ugrile.domain.calendar import (
    SiteDayAssignment,
    assert_coverage,
    check_coverage,
    validate_working_kind,
)
from ugrile.domain.enums import DayStatus, WorkingKind
from ugrile.domain.errors import CoverageInvariantError, ValidationError


def _wd(store: str, person: str, day: int, kind: WorkingKind = WorkingKind.NORMAL):
    return SiteDayAssignment(
        store_id=store,
        person_id=person,
        business_date=date(2026, 8, day),
        status=DayStatus.WORKING,
        working_kind=kind,
    )


def test_no_conflicts_when_one_agent_per_store_day():
    rows = [
        _wd("s1", "a", 1),
        _wd("s2", "c", 1),
        _wd("s1", "a", 2),
    ]
    assert check_coverage(rows) == []


def test_conflict_when_two_agents_share_store_day():
    rows = [
        _wd("s1", "a", 1),
        _wd("s1", "b", 1),
    ]
    conflicts = check_coverage(rows)
    assert len(conflicts) == 1
    assert conflicts[0].code == "MULTIPLE_AGENTS_PER_STORE_DAY"
    assert conflicts[0].store_id == "s1"
    assert conflicts[0].business_date == date(2026, 8, 1)


def test_conflict_when_one_agent_in_two_stores_same_day():
    rows = [
        _wd("s1", "a", 1),
        _wd("s2", "a", 1),
    ]
    conflicts = check_coverage(rows)
    assert len(conflicts) == 1
    assert conflicts[0].code == "MULTIPLE_STORES_PER_AGENT_DAY"
    assert conflicts[0].person_id == "a"


def test_off_and_leave_rows_do_not_trigger_invariants():
    rows = [
        SiteDayAssignment(
            store_id="s1",
            person_id="a",
            business_date=date(2026, 8, 1),
            status=DayStatus.OFF,
            working_kind=None,
        ),
        SiteDayAssignment(
            store_id="s1",
            person_id="b",
            business_date=date(2026, 8, 1),
            status=DayStatus.OFF,
            working_kind=None,
        ),
    ]
    assert check_coverage(rows) == []


def test_assert_coverage_raises_with_details():
    rows = [
        _wd("s1", "a", 1),
        _wd("s1", "b", 1),
    ]
    with pytest.raises(CoverageInvariantError) as ei:
        assert_coverage(rows)
    err = ei.value
    assert err.http_status == 409
    assert err.details["conflicts"][0]["code"] == "MULTIPLE_AGENTS_PER_STORE_DAY"


def test_normal_requires_home_store():
    with pytest.raises(ValidationError):
        validate_working_kind(
            person_home_store_id="s1",
            site_store_id="s2",
            working_kind=WorkingKind.NORMAL,
        )


def test_extra_home_requires_same_home_store():
    with pytest.raises(ValidationError):
        validate_working_kind(
            person_home_store_id="s1",
            site_store_id="s2",
            working_kind=WorkingKind.EXTRA_HOME,
        )


def test_extra_other_requires_different_home_store():
    with pytest.raises(ValidationError):
        validate_working_kind(
            person_home_store_id="s1",
            site_store_id="s1",
            working_kind=WorkingKind.EXTRA_OTHER,
        )


def test_extra_home_ok_when_same_store():
    # No exception.
    validate_working_kind(
        person_home_store_id="s1",
        site_store_id="s1",
        working_kind=WorkingKind.EXTRA_HOME,
    )


@pytest.mark.parametrize(
    "home,site,kind",
    [
        ("s1", "s2", WorkingKind.NORMAL),
        ("s1", "s2", WorkingKind.EXTRA_HOME),
        ("s1", "s1", WorkingKind.EXTRA_OTHER),
        ("s2", "s1", WorkingKind.NORMAL),
    ],
)
def test_invalid_home_other_pairs(home, site, kind):
    """Table-driven coverage of invalid home/other combinations.

    ``NORMAL``/``EXTRA_HOME`` require the person to be on their home store;
    ``EXTRA_OTHER`` requires them to be on a different store. Each row in
    the table is an invariant violation that the domain must reject.
    """

    with pytest.raises(ValidationError):
        validate_working_kind(
            person_home_store_id=home,
            site_store_id=site,
            working_kind=kind,
        )


@pytest.mark.parametrize(
    "home,site,kind",
    [
        ("s1", "s1", WorkingKind.NORMAL),
        ("s1", "s1", WorkingKind.EXTRA_HOME),
        ("s1", "s2", WorkingKind.EXTRA_OTHER),
    ],
)
def test_valid_home_other_pairs(home, site, kind):
    """Table-driven coverage of valid home/other combinations."""

    validate_working_kind(
        person_home_store_id=home,
        site_store_id=site,
        working_kind=kind,
    )
