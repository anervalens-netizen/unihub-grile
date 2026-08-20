"""Concurrent PostgreSQL AC-02 test (S1 attempt-2 remediation).

AC-02 contract: at most one WORKING assignment per store-day AND per
agent-day. The contract is enforced at the DB transaction boundary by the
two partial unique indexes
``uq_site_day_one_working(tenant_id, store_id, business_date) WHERE status = 'WORKING'``
and
``uq_person_day_one_working(tenant_id, person_id, business_date) WHERE status = 'WORKING'``.

The SQLite in-memory path used by the unit tests serialises the writes
through the global write lock, so it cannot reproduce a real cross-process
race. This test wires the application models directly to a real PostgreSQL
database and runs the two races in parallel threads:

* Two threads try to insert a different WORKING assignment for the same
  store-day. Exactly one must commit; the other must hit the partial
  unique index and be rejected with an ``IntegrityError``.
* Two threads try to insert a different WORKING assignment for the same
  agent-day in two different stores. Exactly one must commit; the other
  must be rejected.

The test is skipped when no PostgreSQL server is reachable
(``UGRILE_PG_URL`` unset or connection refused). On the developer's
``dell-standby`` host the ephemeral ``ugrile-pg-s1`` container
(``make pg-up``) satisfies the precondition.
"""

from __future__ import annotations

import contextlib
import threading
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from ugrile.connectors.fixtures import FIXTURE_TENANT_TOKEN
from ugrile.core.database import reset_engine
from ugrile.domain.enums import (
    DayStatus,
    MonthState,
    WorkingKind,
)
from ugrile.domain.identifiers import (
    make_person_id,
    make_store_id,
    make_tenant_id,
)
from ugrile.repositories.models import (
    Month,
    OutboxJob,
    Person,
    SiteDayAssignment,
    Store,
    Tenant,
    User,
)
from ugrile.repositories.months import MonthRepository

pytestmark = pytest.mark.postgres


def _seed(pg_session, *, tenant_token: str = "acme") -> dict[str, str]:
    """Seed a tenant, two stores, and three people. Returns their ids.

    Inserts stores before people so the composite FK on
    ``(tenant_id, home_store_id)`` resolves cleanly. SQLite in-memory tests
    rely on the same ordering through ``add_all`` + flush; PostgreSQL
    enforces the FK strictly regardless of statement order, so the test
    commits the stores first.
    """

    tenant_id = make_tenant_id(tenant_token)
    tenant = Tenant(id=tenant_id, name=tenant_token.title(), timezone="Europe/Bucharest")
    store = Store(
        id=make_store_id(tenant_token, "s1"),
        tenant_id=tenant_id,
        company_code="MOBIUP",
        internal_code="s1",
        name="S1",
    )
    other_store = Store(
        id=make_store_id(tenant_token, "s2"),
        tenant_id=tenant_id,
        company_code="MOBIUP",
        internal_code="s2",
        name="S2",
    )
    user = User(
        id="user_admin",
        tenant_id=tenant_id,
        email="admin@acme.example",
        display_name="Admin",
        role="ADMIN",
    )
    pg_session.add(tenant)
    pg_session.commit()
    pg_session.add_all([store, other_store])
    pg_session.commit()
    pg_session.add(user)
    pg_session.commit()

    person_a = Person(
        id=make_person_id(tenant_token, "a"),
        tenant_id=tenant_id,
        internal_code="a",
        display_name="Alice",
        home_store_id=store.id,
    )
    person_b = Person(
        id=make_person_id(tenant_token, "b"),
        tenant_id=tenant_id,
        internal_code="b",
        display_name="Bob",
        home_store_id=store.id,
    )
    person_c = Person(
        id=make_person_id(tenant_token, "c"),
        tenant_id=tenant_id,
        internal_code="c",
        display_name="Carmen",
        home_store_id=other_store.id,
    )
    pg_session.add(person_a)
    pg_session.commit()
    pg_session.add(person_b)
    pg_session.commit()
    pg_session.add(person_c)
    pg_session.commit()
    return {
        "tenant_id": tenant_id,
        "store_id": store.id,
        "other_store_id": other_store.id,
        "person_a_id": person_a.id,
        "person_b_id": person_b.id,
        "person_c_id": person_c.id,
    }


def _open_month(pg_session, tenant_id: str, year: int, month: int) -> Month:
    m = MonthRepository(pg_session).get_or_create(tenant_id, year, month)
    m.state = MonthState.OPEN
    pg_session.commit()
    return m


def _insert_assignment(
    pg_engine: Any,
    *,
    tenant_id: str,
    month_id: str,
    store_id: str,
    person_id: str,
    business_date: date,
    working_kind: WorkingKind,
    barrier: threading.Barrier,
    outcome: dict[str, BaseException | None],
    name: str,
) -> None:
    """Insert one WORKING row, waiting at a barrier so both threads race.

    Uses its own session so the writes are independent transactions. The
    outcome dict records the exception (or ``None`` for success).
    """

    SessionLocal = sessionmaker(bind=pg_engine, autoflush=False, expire_on_commit=False, future=True)
    session = SessionLocal()
    try:
        barrier.wait()
        row = SiteDayAssignment(
            tenant_id=tenant_id,
            month_id=month_id,
            store_id=store_id,
            person_id=person_id,
            business_date=business_date,
            status=DayStatus.WORKING,
            working_kind=working_kind.value,
            revision=0,
            source=f"PG_RACE_{name}",
        )
        session.add(row)
        session.commit()
        outcome[name] = None
    except BaseException as exc:  # IntegrityError or anything else
        outcome[name] = exc
        with contextlib.suppress(Exception):  # pragma: no cover
            session.rollback()
    finally:
        session.close()


def test_concurrent_inserts_store_day_index_rejects_race(pg_session, pg_engine):
    """Two threads racing on the same store-day must leave exactly one row.

    The race exercises ``uq_site_day_one_working``. PostgreSQL surfaces
    the violation as an ``IntegrityError`` with the index name in
    ``pgerror.diag.constraint_name``.
    """

    reset_engine()  # the SQLite fixture engine must not pollute our process
    ids = _seed(pg_session)
    month = _open_month(pg_session, ids["tenant_id"], 2026, 8)
    barrier = threading.Barrier(2)
    outcome: dict[str, BaseException | None] = {}

    t_a = threading.Thread(
        target=_insert_assignment,
        kwargs=dict(
            pg_engine=pg_engine,
            tenant_id=ids["tenant_id"],
            month_id=month.id,
            store_id=ids["store_id"],
            person_id=ids["person_a_id"],
            business_date=date(2026, 8, 1),
            working_kind=WorkingKind.NORMAL,
            barrier=barrier,
            outcome=outcome,
            name="A",
        ),
    )
    t_b = threading.Thread(
        target=_insert_assignment,
        kwargs=dict(
            pg_engine=pg_engine,
            tenant_id=ids["tenant_id"],
            month_id=month.id,
            store_id=ids["store_id"],
            person_id=ids["person_b_id"],
            business_date=date(2026, 8, 1),
            working_kind=WorkingKind.NORMAL,
            barrier=barrier,
            outcome=outcome,
            name="B",
        ),
    )
    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()

    successes = [name for name, err in outcome.items() if err is None]
    failures = [name for name, err in outcome.items() if err is not None]
    assert len(successes) == 1, f"expected exactly one winner, got {outcome}"
    assert len(failures) == 1
    err = outcome[failures[0]]
    assert isinstance(err, IntegrityError), f"unexpected failure: {err!r}"
    # PostgreSQL surfaces the violated partial index name in the diag.
    diag = getattr(getattr(err, "orig", None), "diag", None)
    index_name = getattr(diag, "constraint_name", "") or ""
    assert "uq_site_day_one_working" in index_name, (
        f"expected uq_site_day_one_working in diag, got: {index_name!r}"
    )

    # The winning row is committed.
    with pg_engine.connect() as conn:
        rows = list(
            conn.execute(
                text(
                    "SELECT person_id FROM site_day_assignments "
                    "WHERE tenant_id = :tid AND store_id = :sid "
                    "AND business_date = :bdate AND status = 'WORKING'"
                ),
                {"tid": ids["tenant_id"], "sid": ids["store_id"], "bdate": date(2026, 8, 1)},
            )
        )
    assert len(rows) == 1


def test_concurrent_inserts_person_day_index_rejects_race(pg_session, pg_engine):
    """Two threads racing on the same agent-day must leave exactly one row.

    The race exercises ``uq_person_day_one_working``.
    """

    reset_engine()
    ids = _seed(pg_session)
    month = _open_month(pg_session, ids["tenant_id"], 2026, 8)
    barrier = threading.Barrier(2)
    outcome: dict[str, BaseException | None] = {}

    t_a = threading.Thread(
        target=_insert_assignment,
        kwargs=dict(
            pg_engine=pg_engine,
            tenant_id=ids["tenant_id"],
            month_id=month.id,
            store_id=ids["store_id"],
            person_id=ids["person_a_id"],
            business_date=date(2026, 8, 2),
            working_kind=WorkingKind.EXTRA_OTHER,
            barrier=barrier,
            outcome=outcome,
            name="A",
        ),
    )
    t_b = threading.Thread(
        target=_insert_assignment,
        kwargs=dict(
            pg_engine=pg_engine,
            tenant_id=ids["tenant_id"],
            month_id=month.id,
            store_id=ids["other_store_id"],
            person_id=ids["person_a_id"],
            business_date=date(2026, 8, 2),
            working_kind=WorkingKind.EXTRA_OTHER,
            barrier=barrier,
            outcome=outcome,
            name="B",
        ),
    )
    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()

    successes = [name for name, err in outcome.items() if err is None]
    failures = [name for name, err in outcome.items() if err is not None]
    assert len(successes) == 1, f"expected exactly one winner, got {outcome}"
    assert len(failures) == 1
    err = outcome[failures[0]]
    assert isinstance(err, IntegrityError), f"unexpected failure: {err!r}"
    diag = getattr(getattr(err, "orig", None), "diag", None)
    index_name = getattr(diag, "constraint_name", "") or ""
    assert "uq_person_day_one_working" in index_name, (
        f"expected uq_person_day_one_working in diag, got: {index_name!r}"
    )


def test_two_tenant_fixture_isolation_on_postgres(pg_session, pg_engine):
    """Two tenants ingesting the same internal codes land in distinct rows.

    This is the AC-03 tenant-safety regression exercised against a real
    PostgreSQL database. The composite ``(tenant_id, internal_code)``
    uniqueness on stores/people plus the tenant-scoped id generator
    guarantee that the two tenants do not collide.
    """

    reset_engine()
    # First tenant via the seed helper.
    ids = _seed(pg_session, tenant_token=FIXTURE_TENANT_TOKEN)
    pg_session.commit()

    SessionLocal = sessionmaker(bind=pg_engine, autoflush=False, expire_on_commit=False, future=True)

    # Second tenant, overlapping internal codes.
    other_token = "beta"
    other_tenant_id = make_tenant_id(other_token)
    other_store_id = make_store_id(other_token, "s1")
    other_person_id = make_person_id(other_token, "a")

    with SessionLocal() as session:
        session.add(
            Tenant(
                id=other_tenant_id,
                name="Beta",
                timezone="Europe/Bucharest",
            )
        )
        session.commit()
        session.add(
            Store(
                id=other_store_id,
                tenant_id=other_tenant_id,
                company_code="BETA",
                internal_code="s1",
                name="Beta S1",
            )
        )
        session.commit()
        session.add(
            Person(
                id=other_person_id,
                tenant_id=other_tenant_id,
                internal_code="a",
                display_name="Beta Alice",
                home_store_id=other_store_id,
            )
        )
        session.commit()

    # The two stores must have different ids; the cross-tenant reference
    # attempt must fail at the DB level (composite FK on
    # people.tenant_id + home_store_id).
    assert ids["store_id"] != other_store_id
    assert ids["person_a_id"] != other_person_id

    from sqlalchemy.exc import IntegrityError as _IE

    with SessionLocal() as session:
        bad = Person(
            id=make_person_id(FIXTURE_TENANT_TOKEN, "rogue"),
            tenant_id=ids["tenant_id"],
            internal_code="rogue",
            display_name="Rogue",
            home_store_id=other_store_id,  # cross-tenant reference
        )
        session.add(bad)
        with pytest.raises(_IE):
            session.commit()
        session.rollback()

    # Sanity: an outbox job row inserted under the first tenant must not
    # be visible when querying under the second tenant.
    with SessionLocal() as session:
        session.add(
            OutboxJob(
                tenant_id=ids["tenant_id"],
                kind="NOOP",
                idempotency_key="acme-only",
                payload="{}",
                status="PENDING",
            )
        )
        session.commit()

    with SessionLocal() as session:
        first = session.query(OutboxJob).filter_by(tenant_id=ids["tenant_id"]).count()
        second = session.query(OutboxJob).filter_by(tenant_id=other_tenant_id).count()
    assert first >= 1
    assert second == 0
