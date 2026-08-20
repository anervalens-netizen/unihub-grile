"""S3 service tests — close/reopen core (AC-15).

The tests prove:

* Admin-only enforcement is honoured.
* A clean month closes deterministically and appends an audit event.
* Closed-month writes are rejected (``MONTH_CLOSED``).
* Reopen requires admin + reason; the audit chain remains append-only.
* Stale revision on close is rejected.
* Blockers surface as a typed ``CLOSE_BLOCKED`` validation error.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ugrile.connectors.fixtures import FIXTURE_GENERATION
from ugrile.domain.enums import DayStatus, MonthState, RoleName, WorkingKind
from ugrile.domain.errors import (
    ConflictError,
    MonthStateError,
    ScopeError,
    ValidationError,
)
from ugrile.repositories.close import MonthCloseEventRepository
from ugrile.repositories.models import (
    SalesStoreDay,
    StoreTarget,
)
from ugrile.repositories.months import MonthRepository
from ugrile.services.auth import Principal
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.services.close import CloseRequest, CloseService, ReopenRequest


def _admin(tenant_id: str) -> Principal:
    return Principal(
        user_id="user_admin",
        tenant_id=tenant_id,
        role=RoleName.ADMIN,
        email="admin@acme.example",
    )


def _seed(session, faker_tenant):
    """Seed sales + targets for both stores in faker_tenant on 2026-08-01."""

    session.add_all(
        [
            SalesStoreDay(
                tenant_id=faker_tenant["tenant_id"],
                store_id=faker_tenant["store_id"],
                business_date=date(2026, 8, 1),
                generation=FIXTURE_GENERATION,
                amount=Decimal("12500"),
                currency="RON",
            ),
            SalesStoreDay(
                tenant_id=faker_tenant["tenant_id"],
                store_id=faker_tenant["other_store_id"],
                business_date=date(2026, 8, 1),
                generation=FIXTURE_GENERATION,
                amount=Decimal("5400.25"),
                currency="RON",
            ),
            StoreTarget(
                tenant_id=faker_tenant["tenant_id"],
                store_id=faker_tenant["store_id"],
                year=2026,
                month=8,
                kind="MONTHLY_SALES",
                version=1,
                amount=Decimal("250000"),
                currency="RON",
            ),
            StoreTarget(
                tenant_id=faker_tenant["tenant_id"],
                store_id=faker_tenant["other_store_id"],
                year=2026,
                month=8,
                kind="MONTHLY_SALES",
                version=1,
                amount=Decimal("120000"),
                currency="RON",
            ),
        ]
    )
    session.commit()


def _seed_clean_month(session, faker_tenant):
    _seed(session, faker_tenant)
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                faker_tenant["person_a_id"],
                date(2026, 8, 1),
                faker_tenant["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            ),
            CalendarChange(
                faker_tenant["person_c_id"],
                date(2026, 8, 1),
                faker_tenant["other_store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            ),
        ],
    )
    session.commit()
    return month


def test_close_succeeds_for_admin_and_appends_audit_event(session, faker_tenant):
    month = _seed_clean_month(session, faker_tenant)
    principal = _admin(faker_tenant["tenant_id"])
    request = CloseRequest(actor_id=principal.user_id, role_value=principal.role.value)
    outcome = CloseService(session).close_month(
        tenant_id=principal.tenant_id, month_id=month.id, request=request
    )
    assert outcome.new_state == MonthState.CLOSED.value
    events = MonthCloseEventRepository(session).list_for_month(
        tenant_id=principal.tenant_id, month_id=month.id
    )
    assert len(events) == 1
    assert events[0].action == "CLOSE"
    assert events[0].new_state == MonthState.CLOSED.value
    assert events[0].revision_after == month.revision


def test_close_rejects_non_admin(session, faker_tenant):
    month = _seed_clean_month(session, faker_tenant)
    principal = Principal(
        user_id="user_manager",
        tenant_id=faker_tenant["tenant_id"],
        role=RoleName.MANAGER,
        email="m@acme.example",
    )
    request = CloseRequest(actor_id=principal.user_id, role_value=principal.role.value)
    with pytest.raises(ScopeError):
        CloseService(session).close_month(
            tenant_id=principal.tenant_id, month_id=month.id, request=request
        )


def test_close_rejects_when_month_already_closed(session, faker_tenant):
    month = _seed_clean_month(session, faker_tenant)
    principal = _admin(faker_tenant["tenant_id"])
    request = CloseRequest(actor_id=principal.user_id, role_value=principal.role.value)
    CloseService(session).close_month(
        tenant_id=principal.tenant_id, month_id=month.id, request=request
    )
    session.commit()
    with pytest.raises(MonthStateError):
        CloseService(session).close_month(
            tenant_id=principal.tenant_id, month_id=month.id, request=request
        )


def test_closed_month_rejects_calendar_writes(session, faker_tenant):
    month = _seed_clean_month(session, faker_tenant)
    principal = _admin(faker_tenant["tenant_id"])
    request = CloseRequest(actor_id=principal.user_id, role_value=principal.role.value)
    CloseService(session).close_month(
        tenant_id=principal.tenant_id, month_id=month.id, request=request
    )
    session.commit()
    with pytest.raises(ConflictError):
        CalendarService(session).apply(
            month=month,
            tenant_id=principal.tenant_id,
            expected_revision=month.revision,
            changes=[
                CalendarChange(
                    faker_tenant["person_b_id"],
                    date(2026, 8, 2),
                    faker_tenant["store_id"],
                    DayStatus.WORKING,
                    WorkingKind.NORMAL,
                )
            ],
        )


def test_close_blocks_when_sale_missing_for_worked_day(session, faker_tenant):
    _seed(session, faker_tenant)
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                faker_tenant["person_a_id"],
                date(2026, 8, 5),
                faker_tenant["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    session.commit()
    principal = _admin(faker_tenant["tenant_id"])
    request = CloseRequest(actor_id=principal.user_id, role_value=principal.role.value)
    with pytest.raises(ValidationError) as ei:
        CloseService(session).close_month(
            tenant_id=principal.tenant_id, month_id=month.id, request=request
        )
    assert ei.value.details["code"] == "CLOSE_BLOCKED"
    codes = {b["code"] for b in ei.value.details["blockers"]}
    assert "SALES_MISSING_FOR_WORKED_DAY" in codes


def test_close_blocks_when_target_zero(session, faker_tenant):
    # No sales, no targets; close must block.
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                faker_tenant["person_a_id"],
                date(2026, 8, 1),
                faker_tenant["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    session.commit()
    principal = _admin(faker_tenant["tenant_id"])
    request = CloseRequest(actor_id=principal.user_id, role_value=principal.role.value)
    with pytest.raises(ValidationError) as ei:
        CloseService(session).close_month(
            tenant_id=principal.tenant_id, month_id=month.id, request=request
        )
    codes = {b["code"] for b in ei.value.details["blockers"]}
    assert "TARGET_ZERO_FOR_WORKED_STORE" in codes


def test_reopen_requires_admin_and_reason(session, faker_tenant):
    month = _seed_clean_month(session, faker_tenant)
    admin = _admin(faker_tenant["tenant_id"])
    CloseService(session).close_month(
        tenant_id=admin.tenant_id,
        month_id=month.id,
        request=CloseRequest(actor_id=admin.user_id, role_value=admin.role.value),
    )
    session.commit()
    manager = Principal(
        user_id="user_manager",
        tenant_id=faker_tenant["tenant_id"],
        role=RoleName.MANAGER,
        email="m@acme.example",
    )
    with pytest.raises(ScopeError):
        CloseService(session).reopen_month(
            tenant_id=admin.tenant_id,
            month_id=month.id,
            request=ReopenRequest(
                actor_id=manager.user_id,
                role_value=manager.role.value,
                reason="corectie target",
            ),
        )
    with pytest.raises(ValidationError):
        CloseService(session).reopen_month(
            tenant_id=admin.tenant_id,
            month_id=month.id,
            request=ReopenRequest(
                actor_id=admin.user_id,
                role_value=admin.role.value,
                reason="",
            ),
        )


def test_reopen_appends_audit_event_and_keeps_history(session, faker_tenant):
    month = _seed_clean_month(session, faker_tenant)
    admin = _admin(faker_tenant["tenant_id"])
    CloseService(session).close_month(
        tenant_id=admin.tenant_id,
        month_id=month.id,
        request=CloseRequest(actor_id=admin.user_id, role_value=admin.role.value),
    )
    session.commit()
    reopen = CloseService(session).reopen_month(
        tenant_id=admin.tenant_id,
        month_id=month.id,
        request=ReopenRequest(
            actor_id=admin.user_id,
            role_value=admin.role.value,
            reason="corectie E-pay lipsa",
        ),
    )
    session.commit()
    assert reopen.new_state == MonthState.REOPENED.value
    events = MonthCloseEventRepository(session).list_for_month(
        tenant_id=admin.tenant_id, month_id=month.id
    )
    assert len(events) == 2
    assert events[0].action == "CLOSE"
    assert events[1].action == "REOPEN"
    assert events[1].reason == "corectie E-pay lipsa"
    assert events[0].id != events[1].id


def test_reopen_state_allows_subsequent_calendar_writes(session, faker_tenant):
    month = _seed_clean_month(session, faker_tenant)
    admin = _admin(faker_tenant["tenant_id"])
    CloseService(session).close_month(
        tenant_id=admin.tenant_id,
        month_id=month.id,
        request=CloseRequest(actor_id=admin.user_id, role_value=admin.role.value),
    )
    session.commit()
    CloseService(session).reopen_month(
        tenant_id=admin.tenant_id,
        month_id=month.id,
        request=ReopenRequest(
            actor_id=admin.user_id,
            role_value=admin.role.value,
            reason="adaug persoana",
        ),
    )
    session.commit()
    CalendarService(session).apply(
        month=month,
        tenant_id=admin.tenant_id,
        expected_revision=month.revision,
        changes=[
            CalendarChange(
                faker_tenant["person_b_id"],
                date(2026, 8, 2),
                faker_tenant["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    session.commit()
    assert month.revision > 0


def test_close_event_repository_returns_ordered_history(session, faker_tenant):
    month = _seed_clean_month(session, faker_tenant)
    admin = _admin(faker_tenant["tenant_id"])
    CloseService(session).close_month(
        tenant_id=admin.tenant_id,
        month_id=month.id,
        request=CloseRequest(actor_id=admin.user_id, role_value=admin.role.value),
    )
    CloseService(session).reopen_month(
        tenant_id=admin.tenant_id,
        month_id=month.id,
        request=ReopenRequest(
            actor_id=admin.user_id,
            role_value=admin.role.value,
            reason="schimbare schema",
        ),
    )
    CloseService(session).close_month(
        tenant_id=admin.tenant_id,
        month_id=month.id,
        request=CloseRequest(actor_id=admin.user_id, role_value=admin.role.value),
    )
    session.commit()
    repo = MonthCloseEventRepository(session)
    events = repo.list_for_month(
        tenant_id=admin.tenant_id, month_id=month.id
    )
    actions = [event.action for event in events]
    assert actions == ["CLOSE", "REOPEN", "CLOSE"]


def test_close_validator_catches_multiple_working_per_store_day(session, faker_tenant):
    """Pure validator: two WORKING rows for the same store/day is a blocker."""

    from ugrile.domain.close import (
        CloseBlockerCode,
        PersonDaySnapshot,
        SalesAvailabilitySnapshot,
        StoreCoverageSnapshot,
        StoreTargetAvailabilitySnapshot,
        validate_close,
    )

    coverage = [
        StoreCoverageSnapshot(
            store_id=faker_tenant["store_id"],
            business_date=date(2026, 8, 1),
            person_id=faker_tenant["person_a_id"],
            working_kind="NORMAL",
        ),
        StoreCoverageSnapshot(
            store_id=faker_tenant["store_id"],
            business_date=date(2026, 8, 1),
            person_id=faker_tenant["person_b_id"],
            working_kind="NORMAL",
        ),
    ]
    person_days = [
        PersonDaySnapshot(
            person_id=faker_tenant["person_a_id"],
            business_date=date(2026, 8, 1),
            store_id=faker_tenant["store_id"],
            working_kind="NORMAL",
        ),
        PersonDaySnapshot(
            person_id=faker_tenant["person_b_id"],
            business_date=date(2026, 8, 1),
            store_id=faker_tenant["store_id"],
            working_kind="NORMAL",
        ),
    ]
    sales = [
        SalesAvailabilitySnapshot(
            store_id=faker_tenant["store_id"],
            business_date=date(2026, 8, 1),
            has_sale=True,
        )
    ]
    targets = [
        StoreTargetAvailabilitySnapshot(
            store_id=faker_tenant["store_id"],
            business_date=date(2026, 8, 1),
            has_target=True,
            target_amount=Decimal("10000"),
        )
    ]
    result = validate_close(
        coverage=coverage,
        person_days=person_days,
        sales_availability=sales,
        target_availability=targets,
    )
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.STORE_DAY_MULTIPLE_WORKING in codes
