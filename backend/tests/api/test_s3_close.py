"""S3 API tests — close / reopen / close-events endpoints."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from ugrile.connectors.fixtures import FIXTURE_GENERATION
from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.domain.rule_pack import RULE_PACK_VERSION
from ugrile.repositories.models import GridCalculation, SalesStoreDay, StoreTarget
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.services.epay import record_readback

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}
MANAGER = {"X-Ugrile-Identity": "user_manager", "X-Ugrile-Tenant": "tenant_acme"}

AUGUST_2026_DAYS = [date(2026, 8, day) for day in range(1, 32)]


def _seed_clean_grid(session, faker_tenant, month) -> None:
    """Seed the exact current-revision grid facts required by final close.

    These API tests exercise close/reopen behavior, not GridService itself, so
    the fixture uses minimal deterministic persisted snapshots with no grid
    anomalies and zero month-bound E-pay inputs.
    """

    rows = (
        (faker_tenant["person_a_id"], faker_tenant["store_id"]),
        (faker_tenant["person_b_id"], faker_tenant["store_id"]),
        (faker_tenant["person_c_id"], faker_tenant["other_store_id"]),
    )
    for index, (person_id, store_id) in enumerate(rows, start=1):
        payload = json.dumps(
            {
                "inputs": {"epay": {"under_50": 0, "at_or_over_50": 0}},
                "components": {},
                "anomalies": [],
            },
            sort_keys=True,
        )
        session.add(
            GridCalculation(
                tenant_id=faker_tenant["tenant_id"],
                month_id=month.id,
                store_id=store_id,
                person_id=person_id,
                rule_pack_version=RULE_PACK_VERSION,
                revision=month.revision,
                inputs_hash=f"{index:x}" * 64,
                outputs_hash=f"{index + 3:x}" * 64,
                payload=payload,
            )
        )
    session.commit()


def _seed_clean_month(client, faker_tenant, session):
    """Seed all final-close inputs and cover both stores for the full month."""

    _ = client
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
    # Final close requires a fresh valid pair for each E-pay category of every
    # person who worked in the store during the month.
    for store_id, person_id in (
        (faker_tenant["store_id"], faker_tenant["person_a_id"]),
        (faker_tenant["other_store_id"], faker_tenant["person_c_id"]),
    ):
        result = record_readback(
            session,
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=store_id,
            actor_id="user_admin",
            observations=[
                {"person_id": person_id, "category": "UNDER_50", "value": 0},
                {"person_id": person_id, "category": "AT_OR_OVER_50", "value": 0},
            ],
        )
        assert result.valid_count == 2
        assert result.invalid_count == 0
    session.commit()
    session.refresh(month)
    _seed_clean_grid(session, faker_tenant, month)
    return month.id


def test_close_requires_admin(engine, faker_tenant, client):
    from ugrile.core import database

    with database.session_scope() as session:
        month_id = _seed_clean_month(client, faker_tenant, session)
    response = client.post(f"/months/{month_id}/close", json={}, headers=MANAGER)
    assert response.status_code == 403, response.text
    response = client.post(f"/months/{month_id}/close", json={}, headers=ADMIN)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["new_state"] == "CLOSED"
    assert body["blockers"] == []


def test_close_blocks_when_sale_missing(engine, faker_tenant, client):
    from ugrile.core import database

    with database.session_scope() as session:
        # Targets are seeded so TARGET_ZERO does not fire — only the
        # missing sale blocker should be reported among the structural facts.
        session.add_all(
            [
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
        month_id = month.id
    response = client.post(f"/months/{month_id}/close", json={}, headers=ADMIN)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["details"]["code"] == "CLOSE_BLOCKED"
    codes = {b["code"] for b in body["details"]["blockers"]}
    assert "SALES_MISSING_FOR_WORKED_DAY" in codes


def test_reopen_requires_admin_and_reason(engine, faker_tenant, client):
    from ugrile.core import database

    with database.session_scope() as session:
        month_id = _seed_clean_month(client, faker_tenant, session)
    close = client.post(f"/months/{month_id}/close", json={}, headers=ADMIN)
    assert close.status_code == 200, close.text

    bad_reason = client.post(
        f"/months/{month_id}/reopen",
        json={"reason": ""},
        headers=ADMIN,
    )
    assert bad_reason.status_code == 422

    manager = client.post(
        f"/months/{month_id}/reopen",
        json={"reason": "corectie E-pay"},
        headers=MANAGER,
    )
    assert manager.status_code == 403, manager.text

    admin = client.post(
        f"/months/{month_id}/reopen",
        json={"reason": "corectie E-pay"},
        headers=ADMIN,
    )
    assert admin.status_code == 200, admin.text
    body = admin.json()
    assert body["new_state"] == "REOPENED"


def test_close_events_endpoint_returns_history(engine, faker_tenant, client):
    from ugrile.core import database

    with database.session_scope() as session:
        month_id = _seed_clean_month(client, faker_tenant, session)
    first_close = client.post(f"/months/{month_id}/close", json={}, headers=ADMIN)
    assert first_close.status_code == 200, first_close.text
    reopen = client.post(
        f"/months/{month_id}/reopen",
        json={"reason": "schimbare schema"},
        headers=ADMIN,
    )
    assert reopen.status_code == 200, reopen.text
    # Reopen advances the authoritative month revision; a new grid snapshot is
    # deliberately required before a second close.
    with database.session_scope() as session:
        month = MonthRepository(session).get(month_id)
        _seed_clean_grid(session, faker_tenant, month)
    second_close = client.post(f"/months/{month_id}/close", json={}, headers=ADMIN)
    assert second_close.status_code == 200, second_close.text
    events = client.get(f"/months/{month_id}/close-events", headers=ADMIN)
    assert events.status_code == 200
    actions = [e["action"] for e in events.json()]
    assert actions == ["CLOSE", "REOPEN", "CLOSE"]
    body = events.json()
    assert body[0]["previous_event_digest"] is None
    assert len(body[0]["event_digest"]) == 64
    assert body[1]["previous_event_digest"] == body[0]["event_digest"]
    assert body[2]["previous_event_digest"] == body[1]["event_digest"]


def test_close_rejects_stale_expected_revision(engine, faker_tenant, client):
    from ugrile.core import database

    with database.session_scope() as session:
        month_id = _seed_clean_month(client, faker_tenant, session)
    response = client.post(
        f"/months/{month_id}/close",
        json={"expected_revision": 0},
        headers=ADMIN,
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "STALE_REVISION"
    assert body["details"]["expected"] == 0
    assert body["details"]["current"] == 1
    # The failed close did not mutate the month.
    months = client.get("/months", headers=ADMIN).json()
    month = next(m for m in months if m["id"] == month_id)
    assert month["state"] == "OPEN"
    assert month["revision"] == 1


def test_close_with_matching_expected_revision_succeeds(engine, faker_tenant, client):
    from ugrile.core import database

    with database.session_scope() as session:
        month_id = _seed_clean_month(client, faker_tenant, session)
    response = client.post(
        f"/months/{month_id}/close",
        json={"expected_revision": 1},
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text
    assert response.json()["new_state"] == "CLOSED"


def test_close_blocks_on_uncovered_store_day_via_api(engine, faker_tenant, client):
    from ugrile.core import database
    from ugrile.repositories.models import SiteDayAssignment

    with database.session_scope() as session:
        month_id = _seed_clean_month(client, faker_tenant, session)
        session.query(SiteDayAssignment).filter_by(
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            business_date=date(2026, 8, 12),
        ).delete()
        session.query(SalesStoreDay).filter_by(
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            business_date=date(2026, 8, 12),
        ).delete()
        session.commit()
    response = client.post(f"/months/{month_id}/close", json={}, headers=ADMIN)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["details"]["code"] == "CLOSE_BLOCKED"
    codes = {b["code"] for b in body["details"]["blockers"]}
    assert "STORE_DAY_UNCOVERED" in codes
