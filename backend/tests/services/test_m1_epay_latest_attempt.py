from __future__ import annotations

from datetime import date

from ugrile.domain.enums import DayStatus, EpayCategory, MonthState, WorkingKind
from ugrile.repositories.epay import latest_snapshot
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.services.epay import freshness_for_month, record_readback


def test_latest_invalid_epay_attempt_blocks_close_freshness_but_preserves_last_good(
    session, faker_tenant
):
    tenant_id = faker_tenant["tenant_id"]
    store_id = faker_tenant["store_id"]
    person_id = faker_tenant["person_a_id"]
    month = MonthRepository(session).get_or_create(tenant_id, 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    CalendarService(session).apply(
        month=month,
        tenant_id=tenant_id,
        expected_revision=0,
        changes=[
            CalendarChange(
                person_id,
                date(2026, 8, 1),
                store_id,
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    session.commit()

    record_readback(
        session,
        tenant_id=tenant_id,
        month_id=month.id,
        store_id=store_id,
        actor_id="user_admin",
        observations=[
            {
                "person_id": person_id,
                "category": EpayCategory.UNDER_50.value,
                "value": 3,
            },
            {
                "person_id": person_id,
                "category": EpayCategory.AT_OR_OVER_50.value,
                "value": 2,
            },
        ],
    )
    session.commit()
    assert freshness_for_month(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        month_id=month.id,
    ).is_fresh

    record_readback(
        session,
        tenant_id=tenant_id,
        month_id=month.id,
        store_id=store_id,
        actor_id="user_admin",
        observations=[
            {
                "person_id": person_id,
                "category": EpayCategory.UNDER_50.value,
                "value": "invalid",
            },
            {
                "person_id": person_id,
                "category": EpayCategory.AT_OR_OVER_50.value,
                "value": 2,
            },
        ],
    )
    session.commit()

    report = freshness_for_month(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        month_id=month.id,
    )
    assert report.is_fresh is False
    assert report.fresh_count == 1
    assert report.expected_count == 2

    # Preview/grid last-good semantics remain intact; final close is blocked by
    # freshness until a new valid readback replaces the invalid latest attempt.
    snapshot = latest_snapshot(
        session,
        tenant_id=tenant_id,
        month_id=month.id,
        store_id=store_id,
        person_id=person_id,
    )
    assert snapshot.under_50_quantity == 3
    assert snapshot.at_or_over_50_quantity == 2
