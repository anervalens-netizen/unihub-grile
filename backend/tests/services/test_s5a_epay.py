"""S5a tests for the E-pay fresh-readback service.

The tests cover the AC-13 contract:

* Valid 0..10 integers per category persist with ``is_valid=True`` and
  feed the grid engine via :func:`latest_snapshot`.
* Invalid inputs — blank, text, fraction, negative, ``>10`` — are
  audited as ``is_valid=False`` with the original ``raw_value``
  preserved; the grid engine ignores them.
* The freshness check returns ``is_fresh=False`` when any
  ``(person, category)`` pair lacks a recent valid observation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ugrile.domain.enums import DayStatus, EpayCategory, WorkingKind
from ugrile.domain.errors import ValidationError
from ugrile.repositories.epay import latest_snapshot
from ugrile.repositories.models import EpayObservation, SiteDayAssignment
from ugrile.repositories.months import MonthRepository
from ugrile.services.epay import freshness_for_month, record_readback


def _open_month_with_working(session, faker_tenant):
    """Open the month and seed one WORKING row for person_a.

    Returns ``(month, assignment)``.
    """

    month = MonthRepository(session).get_or_create(
        faker_tenant["tenant_id"], 2026, 8
    )
    assignment = SiteDayAssignment(
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["store_id"],
        person_id=faker_tenant["person_a_id"],
        business_date=datetime(2026, 8, 1, tzinfo=UTC).date(),
        status=DayStatus.WORKING.value,
        working_kind=WorkingKind.NORMAL.value,
    )
    session.add(assignment)
    session.commit()
    return month, assignment


def test_record_readback_persists_valid_integers(session, faker_tenant):
    month, _ = _open_month_with_working(session, faker_tenant)

    result = record_readback(
        session,
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["store_id"],
        actor_id="user_admin",
        observations=[
            {"person_id": faker_tenant["person_a_id"], "category": "UNDER_50", "value": 0},
            {"person_id": faker_tenant["person_a_id"], "category": "AT_OR_OVER_50", "value": 1},
        ],
    )
    session.commit()
    assert result.valid_count == 2
    assert result.invalid_count == 0
    snapshot = latest_snapshot(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        person_id=faker_tenant["person_a_id"],
    )
    # Both categories survive.
    assert snapshot.under_50_quantity == 0
    assert snapshot.at_or_over_50_quantity == 1

    # A second readback supersedes the first; the snapshot sees the
    # latest valid values per category (last-good retention).
    record_readback(
        session,
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["store_id"],
        actor_id="user_admin",
        observations=[
            {"person_id": faker_tenant["person_a_id"], "category": "UNDER_50", "value": 10},
        ],
    )
    session.commit()
    snapshot = latest_snapshot(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        person_id=faker_tenant["person_a_id"],
    )
    assert snapshot.under_50_quantity == 10
    assert snapshot.at_or_over_50_quantity == 1


def test_record_readback_audits_invalid_inputs(session, faker_tenant):
    month, _ = _open_month_with_working(session, faker_tenant)
    cases = [
        {"person_id": faker_tenant["person_a_id"], "category": "UNDER_50", "value": ""},
        {"person_id": faker_tenant["person_a_id"], "category": "AT_OR_OVER_50", "value": "abc"},
        {"person_id": faker_tenant["person_b_id"], "category": "UNDER_50", "value": 1.5},
        {"person_id": faker_tenant["person_b_id"], "category": "AT_OR_OVER_50", "value": -1},
        {"person_id": faker_tenant["person_c_id"], "category": "UNDER_50", "value": 11},
        {"person_id": faker_tenant["person_c_id"], "category": "AT_OR_OVER_50", "value": None},
    ]
    result = record_readback(
        session,
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["store_id"],
        actor_id="user_admin",
        observations=cases,
    )
    session.commit()
    assert result.valid_count == 0
    assert result.invalid_count == len(cases)
    # Verify the original raw text is preserved verbatim per category.
    raw_by_category = {
        (item.person_id, item.category): item.raw_value for item in result.items
    }
    assert raw_by_category[(faker_tenant["person_a_id"], "UNDER_50")] == ""
    assert raw_by_category[(faker_tenant["person_a_id"], "AT_OR_OVER_50")] == "abc"
    assert raw_by_category[(faker_tenant["person_b_id"], "UNDER_50")] == "1.5"
    assert raw_by_category[(faker_tenant["person_b_id"], "AT_OR_OVER_50")] == "-1"
    assert raw_by_category[(faker_tenant["person_c_id"], "UNDER_50")] == "11"
    # The None input is normalised to a None raw_value (kept as NULL in DB).
    assert raw_by_category[(faker_tenant["person_c_id"], "AT_OR_OVER_50")] is None
    snapshot_a = latest_snapshot(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        person_id=faker_tenant["person_a_id"],
    )
    # No valid observation, snapshot stays at zero.
    assert snapshot_a.under_50_quantity == 0
    assert snapshot_a.at_or_over_50_quantity == 0


def test_record_readback_mixed_valid_invalid(session, faker_tenant):
    month, _ = _open_month_with_working(session, faker_tenant)
    result = record_readback(
        session,
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["store_id"],
        actor_id="user_admin",
        observations=[
            {"person_id": faker_tenant["person_a_id"], "category": "UNDER_50", "value": 5},
            {"person_id": faker_tenant["person_a_id"], "category": "AT_OR_OVER_50", "value": "12"},
        ],
    )
    session.commit()
    assert result.valid_count == 1
    assert result.invalid_count == 1
    invalid_items = [item for item in result.items if not item.is_valid]
    assert len(invalid_items) == 1
    assert invalid_items[0].category == EpayCategory.AT_OR_OVER_50.value
    assert invalid_items[0].raw_value == "12"
    assert invalid_items[0].value is None


def test_record_readback_rejects_empty_observations(session, faker_tenant):
    month, _ = _open_month_with_working(session, faker_tenant)
    with pytest.raises(ValidationError):
        record_readback(
            session,
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["store_id"],
            actor_id="user_admin",
            observations=[],
        )


def test_record_readback_rejects_duplicate_category(session, faker_tenant):
    month, _ = _open_month_with_working(session, faker_tenant)
    with pytest.raises(ValidationError):
        record_readback(
            session,
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["store_id"],
            actor_id="user_admin",
            observations=[
                {"person_id": faker_tenant["person_a_id"], "category": "UNDER_50", "value": 2},
                {"person_id": faker_tenant["person_a_id"], "category": "UNDER_50", "value": 3},
            ],
        )


def test_record_readback_rejects_bad_category(session, faker_tenant):
    month, _ = _open_month_with_working(session, faker_tenant)
    with pytest.raises(ValidationError):
        record_readback(
            session,
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["store_id"],
            actor_id="user_admin",
            observations=[
                {"person_id": faker_tenant["person_a_id"], "category": "WEEKLY", "value": 1},
            ],
        )


def test_freshness_is_false_when_missing(session, faker_tenant):
    month, _ = _open_month_with_working(session, faker_tenant)
    report = freshness_for_month(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        month_id=month.id,
    )
    assert report.expected_count == 2  # one person, two categories
    assert report.fresh_count == 0
    assert report.is_fresh is False


def test_freshness_is_true_after_full_readback(session, faker_tenant):
    month, _ = _open_month_with_working(session, faker_tenant)
    record_readback(
        session,
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["store_id"],
        actor_id="user_admin",
        observations=[
            {"person_id": faker_tenant["person_a_id"], "category": "UNDER_50", "value": 1},
            {"person_id": faker_tenant["person_a_id"], "category": "AT_OR_OVER_50", "value": 2},
        ],
    )
    session.commit()
    report = freshness_for_month(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        month_id=month.id,
    )
    assert report.expected_count == 2
    assert report.fresh_count == 2
    assert report.is_fresh is True


def test_freshness_ignores_stale_rows(session, faker_tenant):
    month, _ = _open_month_with_working(session, faker_tenant)
    stale_at = datetime.now(tz=UTC) - timedelta(days=2)
    session.add(
        EpayObservation(
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            person_id=faker_tenant["person_a_id"],
            category=EpayCategory.UNDER_50.value,
            value=4,
            raw_value="4",
            is_valid=True,
            source="EPAY_READBACK",
            observed_at=stale_at,
        )
    )
    session.commit()
    report = freshness_for_month(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        month_id=month.id,
    )
    assert report.fresh_count == 0
    assert report.is_fresh is False


def test_freshness_ignores_invalid_rows(session, faker_tenant):
    month, _ = _open_month_with_working(session, faker_tenant)
    now = datetime.now(tz=UTC)
    session.add(
        EpayObservation(
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            person_id=faker_tenant["person_a_id"],
            category=EpayCategory.UNDER_50.value,
            value=None,
            raw_value="abc",
            is_valid=False,
            source="EPAY_READBACK",
            observed_at=now,
        )
    )
    session.commit()
    report = freshness_for_month(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        month_id=month.id,
    )
    assert report.fresh_count == 0
    assert report.is_fresh is False
