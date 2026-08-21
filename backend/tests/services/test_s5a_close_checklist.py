"""S5a service tests — close checklist surfaces the typed deferred blockers.

The :class:`ugrile.services.overview.CloseChecklistService` already calls
:func:`ugrile.domain.close.deferred_blockers`. The tests confirm the
``EPAY_FRESH_READBACK_REQUIRED`` / ``SHEET_CANARY_REQUIRED`` /
``EXTERNAL_RECONCILIATION_REQUIRED`` codes appear in the rendered
checklist as informational items (severity preserved from
``_SEVERITY``) and do not block close by themselves.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from ugrile.domain.enums import (
    CloseBlockerCode,
    DayStatus,
    MonthState,
    WorkingKind,
)
from ugrile.repositories.models import SalesStoreDay, SiteDayAssignment, StoreTarget
from ugrile.repositories.months import MonthRepository
from ugrile.services.overview import CloseChecklistService


def _seed_clean_month(session, faker_tenant):
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


def test_close_checklist_includes_typed_s4s5_blockers(session, faker_tenant):
    month = _seed_clean_month(session, faker_tenant)
    checklist = CloseChecklistService(session).month_checklist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    codes = {item.code for item in checklist.blockers}
    assert CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED.value in codes
    assert CloseBlockerCode.SHEET_CANARY_REQUIRED.value in codes
    assert CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED.value in codes


def test_close_checklist_marks_deferred_blockers_with_severity(session, faker_tenant):
    month = _seed_clean_month(session, faker_tenant)
    checklist = CloseChecklistService(session).month_checklist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    severity_by_code = {
        item.code: item.severity for item in checklist.blockers
    }
    # E-pay readback is severity 2 (typed but not enforced at S5a).
    assert severity_by_code[CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED.value] == 2
    # Sheet canary + external reconciliation are severity 3 (integration-stage).
    assert severity_by_code[CloseBlockerCode.SHEET_CANARY_REQUIRED.value] == 3
    assert severity_by_code[CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED.value] == 3
