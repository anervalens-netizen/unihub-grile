"""Real-PostgreSQL persistent Pontaj projection test (S2 remediation).

AC-05 contract: every calendar apply — JSON or XLSX — materialises the full
active-person x every-day-of-month Pontaj lattice in the same transaction as
the calendar CAS. The test drives the service layer against a real PostgreSQL
database:

1. apply an initial schedule (revision 1);
2. read the persisted projection *before* a mid-month edit;
3. perform the mid-month edit (revision 2);
4. read the projection *after*;
5. verify the new revision, per-person totals, 11-hour working rows and that
   revision-1 rows are byte-for-byte unchanged;
6. verify the XLSX contract apply path also materialises rows and that a
   replayed contract is rejected after consumption.

Skipped when no PostgreSQL server is reachable (``UGRILE_PG_URL``).
"""

from __future__ import annotations

from datetime import date
from io import BytesIO

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from ugrile.core.database import reset_engine
from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.domain.errors import ConflictError
from ugrile.domain.identifiers import (
    make_person_id,
    make_store_id,
    make_tenant_id,
)
from ugrile.repositories.models import (
    Month,
    Person,
    Store,
    Tenant,
    User,
)
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.services.schedule_contract import consume_contract, issue_contract, validate_contract
from ugrile.services.schedule_xlsx import build_template, parse_schedule

pytestmark = pytest.mark.postgres

AUGUST_DAYS = 31


def _seed(pg_session) -> dict[str, str]:
    tenant_token = "pontaj"
    tenant_id = make_tenant_id(tenant_token)
    pg_session.add(Tenant(id=tenant_id, name="Pontaj", timezone="Europe/Bucharest"))
    pg_session.commit()
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
    pg_session.add_all([store, other_store])
    pg_session.commit()
    pg_session.add(
        User(
            id="user_admin",
            tenant_id=tenant_id,
            email="admin@acme.example",
            display_name="Admin",
            role="ADMIN",
        )
    )
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
    pg_session.add_all([person_a, person_b, person_c])
    pg_session.commit()
    return {
        "tenant_id": tenant_id,
        "store_id": store.id,
        "other_store_id": other_store.id,
        "person_a_id": person_a.id,
        "person_b_id": person_b.id,
        "person_c_id": person_c.id,
    }


def _open_month(pg_session, tenant_id: str) -> Month:
    month = MonthRepository(pg_session).get_or_create(tenant_id, 2026, 8)
    month.state = MonthState.OPEN
    pg_session.commit()
    return month


def _projection_snapshot(pg_engine, tenant_id: str, month_id: str, revision: int) -> set[tuple]:
    with pg_engine.connect() as conn:
        rows = list(
            conn.execute(
                text(
                    "SELECT person_id, business_date, status, start_time, end_time, "
                    "pause_minutes, hours FROM pontaj_projections "
                    "WHERE tenant_id = :tid AND month_id = :mid AND revision = :rev "
                    "ORDER BY person_id, business_date"
                ),
                {"tid": tenant_id, "mid": month_id, "rev": revision},
            )
        )
    return {(r[0], str(r[1]), r[2], str(r[3]), str(r[4]), r[5], str(r[6])) for r in rows}


def _totals(pg_engine, tenant_id: str, month_id: str, revision: int) -> dict[str, tuple[int, str]]:
    with pg_engine.connect() as conn:
        rows = list(
            conn.execute(
                text(
                    "SELECT person_id, COUNT(*) FILTER (WHERE status = 'WORKING'), "
                    "COALESCE(SUM(hours) FILTER (WHERE status = 'WORKING'), 0) "
                    "FROM pontaj_projections "
                    "WHERE tenant_id = :tid AND month_id = :mid AND revision = :rev "
                    "GROUP BY person_id"
                ),
                {"tid": tenant_id, "mid": month_id, "rev": revision},
            )
        )
    return {r[0]: (int(r[1]), str(r[2])) for r in rows}


def test_pontaj_projection_before_after_mid_month_on_postgres(pg_session, pg_engine):
    reset_engine()
    ids = _seed(pg_session)
    month = _open_month(pg_session, ids["tenant_id"])
    svc = CalendarService(pg_session)

    # Initial schedule (revision 1): Alice works days 1-5, Bob leaves on the
    # 2nd, Carmen is OFF on the 3rd.
    svc.apply(
        month=month,
        tenant_id=ids["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                ids["person_a_id"],
                date(2026, 8, d),
                ids["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
            for d in range(1, 6)
        ]
        + [
            CalendarChange(ids["person_b_id"], date(2026, 8, 2), None, DayStatus.LEAVE),
            CalendarChange(ids["person_c_id"], date(2026, 8, 3), None, DayStatus.OFF),
        ],
    )
    pg_session.commit()
    before = _projection_snapshot(pg_engine, ids["tenant_id"], month.id, 1)
    assert len(before) == 3 * AUGUST_DAYS

    with pg_engine.connect() as conn:
        working_rows = list(
            conn.execute(
                text(
                    "SELECT person_id, hours, start_time, end_time, pause_minutes "
                    "FROM pontaj_projections WHERE tenant_id = :tid AND month_id = :mid "
                    "AND revision = 1 AND status = 'WORKING'"
                ),
                {"tid": ids["tenant_id"], "mid": month.id},
            )
        )
    assert len(working_rows) == 5  # Alice days 1-5
    for _, hours, start, end, pause in working_rows:
        assert hours == 11  # 10:00-22:00 minus 60 minute pause
        assert start is not None and end is not None
        assert pause == 60
    totals_before = _totals(pg_engine, ids["tenant_id"], month.id, 1)
    assert totals_before[ids["person_a_id"]] == (5, "55.00")
    assert totals_before[ids["person_b_id"]] == (0, "0")

    # Mid-month edit (revision 2): Alice stops working the 3rd and starts the 10th.
    svc.apply(
        month=month,
        tenant_id=ids["tenant_id"],
        expected_revision=1,
        changes=[
            CalendarChange(ids["person_a_id"], date(2026, 8, 3), None, DayStatus.OFF),
            CalendarChange(
                ids["person_a_id"],
                date(2026, 8, 10),
                ids["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            ),
        ],
    )
    pg_session.commit()
    after = _projection_snapshot(pg_engine, ids["tenant_id"], month.id, 2)
    assert len(after) == 3 * AUGUST_DAYS
    # Revision 1 rows are immutable: identical before and after the edit.
    assert after != before
    assert _projection_snapshot(pg_engine, ids["tenant_id"], month.id, 1) == before

    totals_after = _totals(pg_engine, ids["tenant_id"], month.id, 2)
    assert totals_after[ids["person_a_id"]] == (5, "55.00")  # {1,2,4,5,10}
    with pg_engine.connect() as conn:
        day3 = conn.execute(
            text(
                "SELECT status, hours FROM pontaj_projections "
                "WHERE tenant_id = :tid AND month_id = :mid AND revision = 2 "
                "AND person_id = :pid AND business_date = '2026-08-03'"
            ),
            {"tid": ids["tenant_id"], "mid": month.id, "pid": ids["person_a_id"]},
        ).fetchone()
        day10 = conn.execute(
            text(
                "SELECT status, hours FROM pontaj_projections "
                "WHERE tenant_id = :tid AND month_id = :mid AND revision = 2 "
                "AND person_id = :pid AND business_date = '2026-08-10'"
            ),
            {"tid": ids["tenant_id"], "mid": month.id, "pid": ids["person_a_id"]},
        ).fetchone()
    assert (day3[0], day3[1]) == ("OFF", 0)
    assert (day10[0], day10[1]) == ("WORKING", 11)

    # The month revision advanced and both projections are readable.
    assert month.revision == 2
    with pg_engine.connect() as conn:
        revisions = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT DISTINCT revision FROM pontaj_projections "
                    "WHERE tenant_id = :tid AND month_id = :mid"
                ),
                {"tid": ids["tenant_id"], "mid": month.id},
            )
        }
    assert revisions == {1, 2}


def test_xlsx_contract_apply_consumes_and_materializes_on_postgres(pg_session, pg_engine):
    reset_engine()
    ids = _seed(pg_session)
    month = _open_month(pg_session, ids["tenant_id"])
    stores = {"s1": ids["store_id"], "s2": ids["other_store_id"]}
    people = [
        {
            "person_id": ids["person_a_id"],
            "display_name": "Alice",
            "home_store_code": "s1",
            "manager_code": "user_admin",
        }
    ]
    allowed_by_date = {
        date(2026, 8, d): {ids["store_id"], ids["other_store_id"]} for d in range(1, 32)
    }
    token = issue_contract(
        pg_session,
        tenant_id=ids["tenant_id"],
        month=month,
        user_id="user_admin",
        base_revision=0,
        stores=stores,
        people=people,
        allowed_by_date=allowed_by_date,
    )
    pg_session.commit()
    data = build_template(
        tenant_id=ids["tenant_id"],
        month_id=month.id,
        year=2026,
        month=8,
        base_revision=0,
        contract_token=token,
        people=people,
        stores=stores,
        allowed_store_ids_by_date=allowed_by_date,
    )
    parsed = parse_schedule(
        BytesIO(data).getvalue(),
        expected_tenant_id=ids["tenant_id"],
        expected_month_id=month.id,
        year=2026,
        month=8,
        stores=stores,
    )
    assert parsed.errors == []
    contract = validate_contract(
        pg_session,
        token=parsed.contract_token,
        tenant_id=ids["tenant_id"],
        month=month,
        user_id="user_admin",
        parsed_tenant_id=parsed.tenant_id,
        parsed_month_id=parsed.month_id,
        parsed_revision=parsed.base_revision,
        stores=stores,
        people=people,
        allowed_by_date=allowed_by_date,
        lock=True,
    )
    result = CalendarService(pg_session).apply(
        month=month,
        tenant_id=ids["tenant_id"],
        changes=parsed.changes,
        expected_revision=contract.base_revision,
        allowed_store_ids_by_date=allowed_by_date,
    )
    consume_contract(pg_session, contract)
    pg_session.commit()
    assert result.revision == 1
    snapshot = _projection_snapshot(pg_engine, ids["tenant_id"], month.id, 1)
    assert len(snapshot) == 3 * AUGUST_DAYS

    # A replay of the consumed contract is rejected deterministically.
    SessionLocal = sessionmaker(
        bind=pg_engine, autoflush=False, expire_on_commit=False, future=True
    )
    with SessionLocal() as session:
        with pytest.raises(ConflictError) as exc:
            validate_contract(
                session,
                token=parsed.contract_token,
                tenant_id=ids["tenant_id"],
                month=session.get(Month, month.id),
                user_id="user_admin",
                parsed_tenant_id=parsed.tenant_id,
                parsed_month_id=parsed.month_id,
                parsed_revision=parsed.base_revision,
                stores=stores,
                people=people,
                allowed_by_date=allowed_by_date,
                lock=False,
            )
        assert exc.value.details["code"] == "CONTRACT_CONSUMED"


def test_failed_apply_on_postgres_rolls_back_projection_and_consumption(pg_session, pg_engine):
    reset_engine()
    ids = _seed(pg_session)
    month = _open_month(pg_session, ids["tenant_id"])
    stores = {"s1": ids["store_id"], "s2": ids["other_store_id"]}
    people = [
        {
            "person_id": ids["person_c_id"],
            "display_name": "Carmen",
            "home_store_code": "s2",
            "manager_code": "user_admin",
        }
    ]
    allowed_by_date = {
        date(2026, 8, d): {ids["store_id"], ids["other_store_id"]} for d in range(1, 32)
    }
    token = issue_contract(
        pg_session,
        tenant_id=ids["tenant_id"],
        month=month,
        user_id="user_admin",
        base_revision=0,
        stores=stores,
        people=people,
        allowed_by_date=allowed_by_date,
    )
    pg_session.commit()
    contract = validate_contract(
        pg_session,
        token=token,
        tenant_id=ids["tenant_id"],
        month=month,
        user_id="user_admin",
        parsed_tenant_id=ids["tenant_id"],
        parsed_month_id=month.id,
        parsed_revision=0,
        stores=stores,
        people=people,
        allowed_by_date=allowed_by_date,
        lock=True,
    )
    narrowed = {date(2026, 8, d): {ids["store_id"]} for d in range(1, 32)}
    from ugrile.domain.errors import ScopeError

    with pytest.raises(ScopeError):
        CalendarService(pg_session).apply(
            month=month,
            tenant_id=ids["tenant_id"],
            changes=[
                CalendarChange(
                    ids["person_c_id"],
                    date(2026, 8, 1),
                    ids["other_store_id"],
                    DayStatus.WORKING,
                    WorkingKind.NORMAL,
                )
            ],
            expected_revision=contract.base_revision,
            allowed_store_ids_by_date=narrowed,
        )
    pg_session.rollback()
    with pg_engine.connect() as conn:
        projection_count = conn.execute(
            text("SELECT COUNT(*) FROM pontaj_projections")
        ).scalar_one()
        contract_state = conn.execute(
            text("SELECT consumed_at FROM schedule_import_contracts " "WHERE token_hash = :th"),
            {"th": contract.token_hash},
        ).scalar_one_or_none()
        month_revision = conn.execute(
            text("SELECT revision FROM months WHERE id = :mid"), {"mid": month.id}
        ).scalar_one()
    assert projection_count == 0
    assert contract_state is None
    assert month_revision == 0
