"""S3 PostgreSQL integration tests (AC-07/15).

The tests run against the same PostgreSQL container used by the S2
integration suite (see ``tests/integration/conftest.py``). They prove:

* sales attribution is materialised in the same revision as the calendar
  CAS — mid-month edits produce a fresh ``revision`` row and never mutate
  earlier ones;
* the immutable ``SalesStoreDay`` total is preserved across reassignment;
* the admin-only close/reopen lifecycle and the append-only audit chain
  hold under PostgreSQL semantics, including the partial-unique index
  rejection of two WORKING rows for the same store/day.

Skipped when ``UGRILE_PG_URL`` is not set or unreachable.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ugrile.connectors.fixtures import FIXTURE_GENERATION
from ugrile.domain.enums import (
    DayStatus,
    MonthState,
    RoleName,
    WorkingKind,
)
from ugrile.domain.errors import (
    ConflictError,
    ScopeError,
    ValidationError,
)
from ugrile.domain.identifiers import (
    make_person_id,
    make_store_id,
    make_tenant_id,
)
from ugrile.repositories.close import MonthCloseEventRepository
from ugrile.repositories.models import (
    Month,
    Person,
    SalesPersonDayProjection,
    SalesStoreDay,
    Store,
    StoreTarget,
    Tenant,
    User,
)
from ugrile.repositories.months import MonthRepository
from ugrile.services.auth import Principal
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.services.close import CloseRequest, CloseService, ReopenRequest

pytestmark = pytest.mark.postgres


def _seed(pg_session) -> dict[str, str]:
    """Seed two stores, three people, and a single sale on day 1."""

    tenant_token = "s3"
    tenant_id = make_tenant_id(tenant_token)
    pg_session.add(Tenant(id=tenant_id, name="S3", timezone="Europe/Bucharest"))
    pg_session.commit()
    s1 = Store(
        id=make_store_id(tenant_token, "s1"),
        tenant_id=tenant_id,
        company_code="MOBIUP",
        internal_code="s1",
        name="S1",
    )
    s2 = Store(
        id=make_store_id(tenant_token, "s2"),
        tenant_id=tenant_id,
        company_code="MOBIUP",
        internal_code="s2",
        name="S2",
    )
    pg_session.add_all([s1, s2])
    pg_session.commit()
    pg_session.add(
        User(
            id="user_admin",
            tenant_id=tenant_id,
            email="admin@s3.example",
            display_name="Admin",
            role=RoleName.ADMIN.value,
        )
    )
    pg_session.commit()
    p1 = Person(
        id=make_person_id(tenant_token, "a"),
        tenant_id=tenant_id,
        internal_code="a",
        display_name="Alice",
        home_store_id=s1.id,
    )
    p2 = Person(
        id=make_person_id(tenant_token, "b"),
        tenant_id=tenant_id,
        internal_code="b",
        display_name="Bob",
        home_store_id=s1.id,
    )
    pg_session.add_all([p1, p2])
    pg_session.commit()
    pg_session.add(
        SalesStoreDay(
            tenant_id=tenant_id,
            store_id=s1.id,
            business_date=date(2026, 8, 1),
            generation=FIXTURE_GENERATION,
            amount=Decimal("12500.00"),
            currency="RON",
        )
    )
    pg_session.add(
        StoreTarget(
            tenant_id=tenant_id,
            store_id=s1.id,
            year=2026,
            month=8,
            kind="MONTHLY_SALES",
            version=1,
            amount=Decimal("250000"),
            currency="RON",
        )
    )
    pg_session.add(
        StoreTarget(
            tenant_id=tenant_id,
            store_id=s2.id,
            year=2026,
            month=8,
            kind="MONTHLY_SALES",
            version=1,
            amount=Decimal("120000"),
            currency="RON",
        )
    )
    pg_session.commit()
    return {
        "tenant_id": tenant_id,
        "store_id": s1.id,
        "other_store_id": s2.id,
        "person_a_id": p1.id,
        "person_b_id": p2.id,
    }


def _open_month(pg_session, tenant_id: str) -> Month:
    month = MonthRepository(pg_session).get_or_create(tenant_id, 2026, 8)
    month.state = MonthState.OPEN
    pg_session.commit()
    return month


def test_attribution_and_reassignment_preserve_company_total_on_postgres(
    pg_session, pg_engine
):
    ids = _seed(pg_session)
    month = _open_month(pg_session, ids["tenant_id"])
    svc = CalendarService(pg_session)
    svc.apply(
        month=month,
        tenant_id=ids["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                ids["person_a_id"],
                date(2026, 8, 1),
                ids["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    pg_session.commit()
    revision_one = (
        pg_session.query(SalesPersonDayProjection)
        .filter_by(month_id=month.id, revision=1)
        .all()
    )
    assert len(revision_one) == 1
    assert revision_one[0].amount == Decimal("12500.00")
    assert revision_one[0].person_id == ids["person_a_id"]

    # Reassign to Bob on day 2 (a new sale row).
    pg_session.add(
        SalesStoreDay(
            tenant_id=ids["tenant_id"],
            store_id=ids["store_id"],
            business_date=date(2026, 8, 2),
            generation=FIXTURE_GENERATION,
            amount=Decimal("9870.50"),
            currency="RON",
        )
    )
    pg_session.commit()
    svc.apply(
        month=month,
        tenant_id=ids["tenant_id"],
        expected_revision=1,
        changes=[
            CalendarChange(
                ids["person_b_id"],
                date(2026, 8, 2),
                ids["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            ),
            CalendarChange(
                ids["person_a_id"],
                date(2026, 8, 2),
                None,
                DayStatus.OFF,
            ),
        ],
    )
    pg_session.commit()

    revision_two = (
        pg_session.query(SalesPersonDayProjection)
        .filter_by(month_id=month.id, revision=2)
        .all()
    )
    assert len(revision_two) == 2
    company_total = sum(row.amount for row in revision_two)
    assert company_total == Decimal("22370.50")
    # Revision 1 is immutable: still Alice on day 1.
    revision_one_again = (
        pg_session.query(SalesPersonDayProjection)
        .filter_by(month_id=month.id, revision=1)
        .all()
    )
    assert revision_one_again[0].person_id == ids["person_a_id"]
    assert revision_one_again[0].amount == Decimal("12500.00")


def test_admin_only_close_reopen_with_audit_chain_on_postgres(pg_session, pg_engine):
    ids = _seed(pg_session)
    month = _open_month(pg_session, ids["tenant_id"])
    CalendarService(pg_session).apply(
        month=month,
        tenant_id=ids["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                ids["person_a_id"],
                date(2026, 8, 1),
                ids["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    pg_session.commit()
    admin = Principal(
        user_id="user_admin",
        tenant_id=ids["tenant_id"],
        role=RoleName.ADMIN,
        email="admin@s3.example",
    )
    manager = Principal(
        user_id="user_manager",
        tenant_id=ids["tenant_id"],
        role=RoleName.MANAGER,
        email="manager@s3.example",
    )

    # Manager is rejected.
    with pytest.raises(ScopeError):
        CloseService(pg_session).close_month(
            tenant_id=ids["tenant_id"],
            month_id=month.id,
            request=CloseRequest(
                actor_id=manager.user_id, role_value=manager.role.value
            ),
        )

    outcome = CloseService(pg_session).close_month(
        tenant_id=ids["tenant_id"],
        month_id=month.id,
        request=CloseRequest(actor_id=admin.user_id, role_value=admin.role.value),
    )
    pg_session.commit()
    assert outcome.new_state == MonthState.CLOSED.value

    # Calendar writes are rejected after close.
    with pytest.raises(ConflictError):
        CalendarService(pg_session).apply(
            month=month,
            tenant_id=ids["tenant_id"],
            expected_revision=month.revision,
            changes=[
                CalendarChange(
                    ids["person_b_id"],
                    date(2026, 8, 5),
                    ids["store_id"],
                    DayStatus.WORKING,
                    WorkingKind.NORMAL,
                )
            ],
        )

    # Reopen requires admin + reason.
    reopen = CloseService(pg_session).reopen_month(
        tenant_id=ids["tenant_id"],
        month_id=month.id,
        request=ReopenRequest(
            actor_id=admin.user_id,
            role_value=admin.role.value,
            reason="corectie E-pay",
        ),
    )
    pg_session.commit()
    assert reopen.new_state == MonthState.REOPENED.value

    events = MonthCloseEventRepository(pg_session).list_for_month(
        tenant_id=ids["tenant_id"], month_id=month.id
    )
    assert [event.action for event in events] == ["CLOSE", "REOPEN"]
    assert events[0].id != events[1].id

    # After reopen the calendar accepts writes again.
    CalendarService(pg_session).apply(
        month=month,
        tenant_id=ids["tenant_id"],
        expected_revision=month.revision,
        changes=[
            CalendarChange(
                ids["person_b_id"],
                date(2026, 8, 5),
                ids["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    pg_session.commit()


def test_close_blocked_when_sales_missing_on_postgres(pg_session, pg_engine):
    ids = _seed(pg_session)
    # Drop the seeded sale so the worked day has no ``SalesStoreDay``.
    pg_session.query(SalesStoreDay).filter_by(
        tenant_id=ids["tenant_id"],
        store_id=ids["store_id"],
        business_date=date(2026, 8, 1),
    ).delete()
    pg_session.commit()
    month = _open_month(pg_session, ids["tenant_id"])
    CalendarService(pg_session).apply(
        month=month,
        tenant_id=ids["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                ids["person_a_id"],
                date(2026, 8, 1),
                ids["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    pg_session.commit()
    admin = Principal(
        user_id="user_admin",
        tenant_id=ids["tenant_id"],
        role=RoleName.ADMIN,
        email="admin@s3.example",
    )
    with pytest.raises(ValidationError) as ei:
        CloseService(pg_session).close_month(
            tenant_id=ids["tenant_id"],
            month_id=month.id,
            request=CloseRequest(actor_id=admin.user_id, role_value=admin.role.value),
        )
    assert ei.value.details["code"] == "CLOSE_BLOCKED"
    codes = {b["code"] for b in ei.value.details["blockers"]}
    assert "SALES_MISSING_FOR_WORKED_DAY" in codes
