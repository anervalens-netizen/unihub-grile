"""S3 AC-15 remediation — PostgreSQL integration tests (AC-15).

Proves against real PostgreSQL semantics:

* the full open-store/day lattice is validated: an active store/day with no
  assignment row blocks close with ``STORE_DAY_UNCOVERED`` (finding 1);
* two concurrent close attempts serialize on the ``Month`` row lock:
  exactly one succeeds, the loser observes the committed ``CLOSED`` state
  and raises ``MonthStateError``, and exactly one audit event is appended
  (finding 2);
* the append-only audit chain stays digest-verifiable after the
  close/reopen/close lifecycle (finding 3).

Skipped when ``UGRILE_PG_URL`` is not set or unreachable.
"""

from __future__ import annotations

import contextlib
import threading
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.orm import sessionmaker

from ugrile.connectors.fixtures import FIXTURE_GENERATION
from ugrile.core.database import reset_engine
from ugrile.domain.enums import (
    DayStatus,
    MonthState,
    RoleName,
    WorkingKind,
)
from ugrile.domain.errors import MonthStateError
from ugrile.domain.identifiers import (
    make_person_id,
    make_store_id,
    make_tenant_id,
)
from ugrile.repositories.close import MonthCloseEventRepository
from ugrile.repositories.models import (
    Month,
    Person,
    SalesStoreDay,
    Store,
    StoreTarget,
    Tenant,
    User,
)
from ugrile.repositories.months import MonthRepository
from ugrile.services.auth import Principal
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.services.close import CloseOutcome, CloseRequest, CloseService

pytestmark = pytest.mark.postgres

AUGUST_2026_DAYS = [date(2026, 8, day) for day in range(1, 32)]


def _seed(pg_session) -> dict[str, str]:
    """Seed a tenant, two active stores, three people and an admin user."""

    tenant_token = "s3ac15"
    tenant_id = make_tenant_id(tenant_token)
    pg_session.add(Tenant(id=tenant_id, name="S3 AC15", timezone="Europe/Bucharest"))
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
            email="admin@s3ac15.example",
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
    p3 = Person(
        id=make_person_id(tenant_token, "c"),
        tenant_id=tenant_id,
        internal_code="c",
        display_name="Carmen",
        home_store_id=s2.id,
    )
    pg_session.add_all([p1, p2, p3])
    pg_session.commit()
    return {
        "tenant_id": tenant_id,
        "store_id": s1.id,
        "other_store_id": s2.id,
        "person_a_id": p1.id,
        "person_b_id": p2.id,
        "person_c_id": p3.id,
    }


def _seed_clean_full_month(pg_session, ids: dict[str, str]) -> Month:
    """Seed sales/targets for every day and cover both stores all month."""

    pg_session.add_all(
        [
            SalesStoreDay(
                tenant_id=ids["tenant_id"],
                store_id=ids["store_id"],
                business_date=d,
                generation=FIXTURE_GENERATION,
                amount=Decimal("12500.00"),
                currency="RON",
            )
            for d in AUGUST_2026_DAYS
        ]
        + [
            SalesStoreDay(
                tenant_id=ids["tenant_id"],
                store_id=ids["other_store_id"],
                business_date=d,
                generation=FIXTURE_GENERATION,
                amount=Decimal("5400.25"),
                currency="RON",
            )
            for d in AUGUST_2026_DAYS
        ]
        + [
            StoreTarget(
                tenant_id=ids["tenant_id"],
                store_id=ids["store_id"],
                year=2026,
                month=8,
                kind="MONTHLY_SALES",
                version=1,
                amount=Decimal("250000"),
                currency="RON",
            ),
            StoreTarget(
                tenant_id=ids["tenant_id"],
                store_id=ids["other_store_id"],
                year=2026,
                month=8,
                kind="MONTHLY_SALES",
                version=1,
                amount=Decimal("120000"),
                currency="RON",
            ),
        ]
    )
    pg_session.commit()
    month = MonthRepository(pg_session).get_or_create(ids["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    pg_session.commit()
    CalendarService(pg_session).apply(
        month=month,
        tenant_id=ids["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                ids["person_a_id"], d, ids["store_id"], DayStatus.WORKING, WorkingKind.NORMAL
            )
            for d in AUGUST_2026_DAYS
        ]
        + [
            CalendarChange(
                ids["person_c_id"], d, ids["other_store_id"], DayStatus.WORKING, WorkingKind.NORMAL
            )
            for d in AUGUST_2026_DAYS
        ],
    )
    pg_session.commit()
    return month


def _admin(tenant_id: str) -> Principal:
    return Principal(
        user_id="user_admin",
        tenant_id=tenant_id,
        role=RoleName.ADMIN,
        email="admin@s3ac15.example",
    )


def test_full_lattice_uncovered_blocks_close_on_postgres(pg_session, pg_engine):
    ids = _seed(pg_session)
    month = _seed_clean_full_month(pg_session, ids)
    # Remove the WORKING assignment for store 1 on 2026-08-15 and its sale,
    # so the remaining blocker for that day is the missing assignment row.
    from sqlalchemy import delete

    from ugrile.repositories.models import SiteDayAssignment

    pg_session.execute(
        delete(SiteDayAssignment).where(
            SiteDayAssignment.tenant_id == ids["tenant_id"],
            SiteDayAssignment.store_id == ids["store_id"],
            SiteDayAssignment.business_date == date(2026, 8, 15),
        )
    )
    pg_session.execute(
        delete(SalesStoreDay).where(
            SalesStoreDay.tenant_id == ids["tenant_id"],
            SalesStoreDay.store_id == ids["store_id"],
            SalesStoreDay.business_date == date(2026, 8, 15),
        )
    )
    pg_session.commit()

    admin = _admin(ids["tenant_id"])
    from ugrile.domain.errors import ValidationError

    with pytest.raises(ValidationError) as ei:
        CloseService(pg_session).close_month(
            tenant_id=ids["tenant_id"],
            month_id=month.id,
            request=CloseRequest(actor_id=admin.user_id, role_value=admin.role.value),
        )
    assert ei.value.details["code"] == "CLOSE_BLOCKED"
    blockers = ei.value.details["blockers"]
    uncovered = [
        b
        for b in blockers
        if b["code"] == "STORE_DAY_UNCOVERED"
        and b["store_id"] == ids["store_id"]
        and b["business_date"] == "2026-08-15"
    ]
    assert uncovered, f"expected STORE_DAY_UNCOVERED for store 1 on 2026-08-15, got {blockers}"
    # The failed close must not mutate the month.
    pg_session.rollback()
    fresh = MonthRepository(pg_session).get(month.id)
    assert fresh.state == MonthState.OPEN.value
    assert (
        MonthCloseEventRepository(pg_session).list_for_month(
            tenant_id=ids["tenant_id"], month_id=month.id
        )
        == []
    )


def _attempt_close(
    pg_engine: Any,
    *,
    tenant_id: str,
    month_id: str,
    actor_id: str,
    barrier: threading.Barrier,
    outcome: dict[str, BaseException | CloseOutcome | None],
    name: str,
) -> None:
    """Attempt one close in its own session, racing at the barrier."""

    SessionLocal = sessionmaker(
        bind=pg_engine, autoflush=False, expire_on_commit=False, future=True
    )
    session = SessionLocal()
    try:
        barrier.wait()
        result = CloseService(session).close_month(
            tenant_id=tenant_id,
            month_id=month_id,
            request=CloseRequest(actor_id=actor_id, role_value=RoleName.ADMIN.value),
        )
        session.commit()
        outcome[name] = result
    except BaseException as exc:
        outcome[name] = exc
        with contextlib.suppress(Exception):  # pragma: no cover
            session.rollback()
    finally:
        session.close()


def test_two_concurrent_close_attempts_serialize_on_postgres(pg_session, pg_engine):
    """Two racing close attempts: exactly one succeeds, one sees CLOSED.

    The ``Month`` row is acquired ``SELECT ... FOR UPDATE`` before any
    state decision, so the loser blocks on the row lock and then observes
    the committed ``CLOSED`` state (``MonthStateError``).
    """

    reset_engine()
    ids = _seed(pg_session)
    month = _seed_clean_full_month(pg_session, ids)
    assert month.revision == 1

    barrier = threading.Barrier(2)
    outcome: dict[str, BaseException | CloseOutcome | None] = {}

    threads = [
        threading.Thread(
            target=_attempt_close,
            kwargs=dict(
                pg_engine=pg_engine,
                tenant_id=ids["tenant_id"],
                month_id=month.id,
                actor_id="user_admin",
                barrier=barrier,
                outcome=outcome,
                name=name,
            ),
        )
        for name in ("A", "B")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    successes = [name for name, value in outcome.items() if isinstance(value, CloseOutcome)]
    failures = [name for name, value in outcome.items() if not isinstance(value, CloseOutcome)]
    assert len(successes) == 1, f"expected exactly one winner, got {outcome}"
    assert len(failures) == 1, f"expected exactly one loser, got {outcome}"
    loser = outcome[failures[0]]
    assert isinstance(loser, MonthStateError), f"unexpected loser error: {loser!r}"

    # The month is immutably closed with exactly one audit event.
    pg_session.expire_all()  # drop the fixture session's stale identity map
    fresh = MonthRepository(pg_session).get(month.id)
    assert fresh.state == MonthState.CLOSED.value
    assert fresh.revision == 2
    events = MonthCloseEventRepository(pg_session).list_for_month(
        tenant_id=ids["tenant_id"], month_id=month.id
    )
    assert [event.action for event in events] == ["CLOSE"]
    assert (
        MonthCloseEventRepository(pg_session).verify_chain(
            tenant_id=ids["tenant_id"], month_id=month.id
        )
        == []
    )


def test_close_chain_digest_holds_on_postgres(pg_session, pg_engine):
    """Close/reopen/close appends a digest-verifiable append-only chain."""

    ids = _seed(pg_session)
    month = _seed_clean_full_month(pg_session, ids)
    admin = _admin(ids["tenant_id"])
    from ugrile.services.close import ReopenRequest

    CloseService(pg_session).close_month(
        tenant_id=ids["tenant_id"],
        month_id=month.id,
        request=CloseRequest(actor_id=admin.user_id, role_value=admin.role.value),
    )
    pg_session.commit()
    CloseService(pg_session).reopen_month(
        tenant_id=ids["tenant_id"],
        month_id=month.id,
        request=ReopenRequest(
            actor_id=admin.user_id,
            role_value=admin.role.value,
            reason="corectie E-pay",
        ),
    )
    pg_session.commit()
    CloseService(pg_session).close_month(
        tenant_id=ids["tenant_id"],
        month_id=month.id,
        request=CloseRequest(actor_id=admin.user_id, role_value=admin.role.value),
    )
    pg_session.commit()
    repo = MonthCloseEventRepository(pg_session)
    events = repo.list_for_month(tenant_id=ids["tenant_id"], month_id=month.id)
    assert [event.action for event in events] == ["CLOSE", "REOPEN", "CLOSE"]
    assert repo.verify_chain(tenant_id=ids["tenant_id"], month_id=month.id) == []
    assert events[1].previous_event_digest == events[0].event_digest
    assert events[2].previous_event_digest == events[1].event_digest
