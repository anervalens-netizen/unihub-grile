"""S3 service tests — close/reopen core (AC-15).

The tests prove:

* Admin-only enforcement is honoured.
* A clean month closes deterministically and appends an audit event.
* Closed-month writes are rejected (``MONTH_CLOSED``).
* Reopen requires admin + reason; the audit chain remains append-only.
* Stale revision on close is rejected against the locked month row.
* Blockers surface as a typed ``CLOSE_BLOCKED`` validation error and never
  mutate state or the audit chain.
* The full open-store/day lattice is validated: an active store/day with no
  assignment row is a ``STORE_DAY_UNCOVERED`` blocker (finding 1).
* The close audit chain is chained by deterministic digests and tampering
  is detected by ``verify_chain``.
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
    StaleRevisionError,
    ValidationError,
)
from ugrile.repositories.close import MonthCloseEventRepository
from ugrile.repositories.models import (
    SalesStoreDay,
    SiteDayAssignment,
    StoreTarget,
)
from ugrile.repositories.months import MonthRepository
from ugrile.services.auth import Principal
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.services.close import CloseRequest, CloseService, ReopenRequest

AUGUST_2026_DAYS = [date(2026, 8, day) for day in range(1, 32)]


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


def _seed_full_month_sales(session, faker_tenant):
    """Seed a SalesStoreDay for every day of August 2026 for both stores."""

    session.add_all(
        [
            SalesStoreDay(
                tenant_id=faker_tenant["tenant_id"],
                store_id=faker_tenant["store_id"],
                business_date=d,
                generation=FIXTURE_GENERATION,
                amount=Decimal("12500"),
                currency="RON",
            )
            for d in AUGUST_2026_DAYS
        ]
        + [
            SalesStoreDay(
                tenant_id=faker_tenant["tenant_id"],
                store_id=faker_tenant["other_store_id"],
                business_date=d,
                generation=FIXTURE_GENERATION,
                amount=Decimal("5400.25"),
                currency="RON",
            )
            for d in AUGUST_2026_DAYS
        ]
        + [
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


def _cover_full_month(session, month, faker_tenant):
    """Apply WORKING assignments for every August 2026 day on both stores.

    The S3 open-store/day authority is every active store for every date of
    the month, so a clean close fixture must represent the intended open
    days explicitly — one working agent per store/day across the whole
    month, with no OFF/LEAVE hole.
    """

    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                faker_tenant["person_a_id"],
                d,
                faker_tenant["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
            for d in AUGUST_2026_DAYS
        ]
        + [
            CalendarChange(
                faker_tenant["person_c_id"],
                d,
                faker_tenant["other_store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
            for d in AUGUST_2026_DAYS
        ],
    )


def _seed_clean_month(session, faker_tenant):
    _seed_full_month_sales(session, faker_tenant)
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    _cover_full_month(session, month, faker_tenant)
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
    # Day 2 of store 1 is already covered by person_a in the full-month
    # fixture, so person_b replaces person_a (person_a goes OFF that day).
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
    events = repo.list_for_month(tenant_id=admin.tenant_id, month_id=month.id)
    actions = [event.action for event in events]
    assert actions == ["CLOSE", "REOPEN", "CLOSE"]


def test_close_validator_catches_multiple_working_per_store_day(session, faker_tenant):
    """Pure validator: two WORKING rows for the same store/day is a blocker."""

    from ugrile.domain.close import (
        CloseBlockerCode,
        OpenStoreDay,
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
        open_days=[
            OpenStoreDay(
                store_id=faker_tenant["store_id"],
                business_date=date(2026, 8, 1),
            )
        ],
        coverage=coverage,
        person_days=person_days,
        sales_availability=sales,
        target_availability=targets,
    )
    codes = {b.code for b in result.blockers}
    assert CloseBlockerCode.STORE_DAY_MULTIPLE_WORKING in codes


def test_close_blocks_on_uncovered_store_day(session, faker_tenant):
    """Finding 1: an active store/day with no assignment row blocks close."""

    month = _seed_clean_month(session, faker_tenant)
    # Remove the WORKING assignment for store 1 on 2026-08-10 and its sale,
    # so the only source-driven blocker for that day is STORE_DAY_UNCOVERED.
    session.query(SiteDayAssignment).filter_by(
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        business_date=date(2026, 8, 10),
    ).delete()
    session.query(SalesStoreDay).filter_by(
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        business_date=date(2026, 8, 10),
    ).delete()
    session.commit()
    admin = _admin(faker_tenant["tenant_id"])
    with pytest.raises(ValidationError) as ei:
        CloseService(session).close_month(
            tenant_id=admin.tenant_id,
            month_id=month.id,
            request=CloseRequest(actor_id=admin.user_id, role_value=admin.role.value),
        )
    assert ei.value.details["code"] == "CLOSE_BLOCKED"
    codes = {b["code"] for b in ei.value.details["blockers"]}
    assert "STORE_DAY_UNCOVERED" in codes
    uncovered = [
        b
        for b in ei.value.details["blockers"]
        if b["code"] == "STORE_DAY_UNCOVERED"
        and b["store_id"] == faker_tenant["store_id"]
        and b["business_date"] == "2026-08-10"
    ]
    assert uncovered, "expected STORE_DAY_UNCOVERED for store 1 on 2026-08-10"


def test_close_blocked_does_not_mutate_state_or_audit(session, faker_tenant):
    """A blocker failure must not change state, revision or the audit chain."""

    month = _seed_clean_month(session, faker_tenant)
    session.query(SalesStoreDay).filter_by(
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        business_date=date(2026, 8, 3),
    ).delete()
    session.commit()
    revision_before = month.revision
    admin = _admin(faker_tenant["tenant_id"])
    with pytest.raises(ValidationError):
        CloseService(session).close_month(
            tenant_id=admin.tenant_id,
            month_id=month.id,
            request=CloseRequest(actor_id=admin.user_id, role_value=admin.role.value),
        )
    session.rollback()
    fresh = MonthRepository(session).get(month.id)
    assert fresh.state == MonthState.OPEN.value
    assert fresh.revision == revision_before
    events = MonthCloseEventRepository(session).list_for_month(
        tenant_id=admin.tenant_id, month_id=month.id
    )
    assert events == []


def test_close_rejects_stale_expected_revision(session, faker_tenant):
    """expected_revision is validated against the locked month row."""

    month = _seed_clean_month(session, faker_tenant)
    assert month.revision == 1  # one calendar CAS applied
    admin = _admin(faker_tenant["tenant_id"])
    with pytest.raises(StaleRevisionError) as ei:
        CloseService(session).close_month(
            tenant_id=admin.tenant_id,
            month_id=month.id,
            request=CloseRequest(
                actor_id=admin.user_id,
                role_value=admin.role.value,
                expected_revision=0,
            ),
        )
    assert ei.value.details["code"] == "STALE_REVISION"
    assert ei.value.details["expected"] == 0
    assert ei.value.details["current"] == 1
    session.rollback()
    fresh = MonthRepository(session).get(month.id)
    assert fresh.state == MonthState.OPEN.value
    assert fresh.revision == 1


def test_close_with_matching_expected_revision_succeeds(session, faker_tenant):
    month = _seed_clean_month(session, faker_tenant)
    admin = _admin(faker_tenant["tenant_id"])
    outcome = CloseService(session).close_month(
        tenant_id=admin.tenant_id,
        month_id=month.id,
        request=CloseRequest(
            actor_id=admin.user_id,
            role_value=admin.role.value,
            expected_revision=month.revision,
        ),
    )
    assert outcome.new_state == MonthState.CLOSED.value


def test_close_chain_digest_verifies_and_detects_tampering(session, faker_tenant):
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
            reason="corectie E-pay",
        ),
    )
    CloseService(session).close_month(
        tenant_id=admin.tenant_id,
        month_id=month.id,
        request=CloseRequest(actor_id=admin.user_id, role_value=admin.role.value),
    )
    session.commit()
    repo = MonthCloseEventRepository(session)
    events = repo.list_for_month(tenant_id=admin.tenant_id, month_id=month.id)
    assert len(events) == 3
    assert repo.verify_chain(tenant_id=admin.tenant_id, month_id=month.id) == []
    # Every event carries a 64-hex digest; the first has no previous digest.
    assert events[0].previous_event_digest is None
    assert len(events[0].event_digest) == 64
    assert events[1].previous_event_digest == events[0].event_digest
    assert events[2].previous_event_digest == events[1].event_digest

    # Tamper with the first event's reason; the chain must flag it.
    events[0].reason = "tampered"
    session.flush()
    issues = repo.verify_chain(tenant_id=admin.tenant_id, month_id=month.id)
    assert any("event_digest mismatch" in issue for issue in issues)
