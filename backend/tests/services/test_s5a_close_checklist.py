"""Close-checklist tests for policy/freshness consistency."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from ugrile.domain.enums import CloseBlockerCode, DayStatus, MonthState, WorkingKind
from ugrile.repositories.models import SalesStoreDay, SiteDayAssignment, StoreTarget
from ugrile.repositories.months import MonthRepository
from ugrile.services.epay import record_readback
from ugrile.services.policy_checklist import PolicyCloseChecklistService


def _seed_month(session, faker_tenant):
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    days = [datetime(2026, 8, day, tzinfo=UTC).date() for day in range(1, 32)]
    session.add_all(
        [
            SalesStoreDay(
                tenant_id=faker_tenant["tenant_id"],
                store_id=faker_tenant["store_id"],
                business_date=d,
                generation="FIXTURE_V1",
                amount=Decimal("100"),
                currency="RON",
            )
            for d in days
        ]
    )
    session.add(
        StoreTarget(
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            year=2026,
            month=8,
            kind="MONTHLY_SALES",
            version=1,
            amount=Decimal("2000"),
            currency="RON",
        )
    )
    session.add(
        SiteDayAssignment(
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["store_id"],
            person_id=faker_tenant["person_a_id"],
            business_date=datetime(2026, 8, 1, tzinfo=UTC).date(),
            status=DayStatus.WORKING.value,
            working_kind=WorkingKind.NORMAL.value,
        )
    )
    session.commit()
    return month


def _checklist(session, faker_tenant, month):
    return PolicyCloseChecklistService(session).month_checklist(
        tenant_id=faker_tenant["tenant_id"],
        month=month,
    )


def test_stale_epay_is_visible_and_blocking(session, faker_tenant):
    month = _seed_month(session, faker_tenant)
    checklist = _checklist(session, faker_tenant, month)
    epay_items = [
        item
        for item in checklist.blockers
        if item.code == CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED.value
    ]
    assert epay_items
    assert all(item.blocking for item in epay_items)


def test_fresh_epay_removes_store_epay_blocker(session, faker_tenant):
    month = _seed_month(session, faker_tenant)
    result = record_readback(
        session,
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["store_id"],
        actor_id="user_admin",
        observations=[
            {
                "person_id": faker_tenant["person_a_id"],
                "category": "UNDER_50",
                "value": 0,
            },
            {
                "person_id": faker_tenant["person_a_id"],
                "category": "AT_OR_OVER_50",
                "value": 0,
            },
        ],
    )
    assert result.valid_count == 2
    session.commit()

    checklist = _checklist(session, faker_tenant, month)
    store_epay_items = [
        item
        for item in checklist.blockers
        if item.code == CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED.value
        and faker_tenant["store_id"] in item.detail
    ]
    assert store_epay_items == []


def test_sheet_and_external_evidence_are_visible_warnings(session, faker_tenant):
    month = _seed_month(session, faker_tenant)
    checklist = _checklist(session, faker_tenant, month)
    by_code = {item.code: item for item in checklist.blockers}
    assert by_code[CloseBlockerCode.SHEET_CANARY_REQUIRED.value].blocking is False
    assert by_code[CloseBlockerCode.SHEET_CANARY_REQUIRED.value].severity == 3
    assert by_code[CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED.value].blocking is False
    assert by_code[CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED.value].severity == 3
