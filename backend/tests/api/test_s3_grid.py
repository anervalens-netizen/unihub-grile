"""S3 API tests — grid / salary / attribution endpoints.

The tests cover:

* ``POST /months/{id}/grid/compute`` admin-only writes one row per person.
* ``POST /months/{id}/salary`` admin-only upserts a salary window.
* ``GET /months/{id}/attribution`` returns the attributed projection.
* ``GET /months/{id}/grid`` returns the persisted grid rows.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ugrile.connectors.fixtures import FIXTURE_GENERATION
from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.repositories.models import (
    SalesStoreDay,
    StoreTarget,
)
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}
MANAGER = {"X-Ugrile-Identity": "user_manager", "X-Ugrile-Tenant": "tenant_acme"}


def _open_month_with_calendar(client, faker_tenant, session):
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    session.add(SalesStoreDay(
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        business_date=date(2026, 8, 1),
        generation=FIXTURE_GENERATION,
        amount=Decimal("12500"),
        currency="RON",
    ))
    session.add(StoreTarget(
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        year=2026, month=8,
        kind="MONTHLY_SALES",
        version=1,
        amount=Decimal("250000"),
        currency="RON",
    ))
    session.commit()
    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=0,
        changes=[CalendarChange(
            faker_tenant["person_a_id"], date(2026, 8, 1),
            faker_tenant["store_id"],
            DayStatus.WORKING, WorkingKind.NORMAL,
        )],
    )
    session.commit()
    return month.id


def test_grid_compute_requires_admin(engine, faker_tenant, client):
    from ugrile.core import database
    with database.session_scope() as session:
        month_id = _open_month_with_calendar(client, faker_tenant, session)
    response = client.post(f"/months/{month_id}/grid/compute", headers=MANAGER)
    assert response.status_code == 403
    response = client.post(f"/months/{month_id}/grid/compute", headers=ADMIN)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["month_id"] == month_id
    assert len(body["snapshots"]) == 3
    assert body["rule_pack_version"] == "mobiup-v1-compat"


def test_grid_get_returns_persisted_rows(engine, faker_tenant, client):
    from ugrile.core import database
    with database.session_scope() as session:
        month_id = _open_month_with_calendar(client, faker_tenant, session)
    compute = client.post(f"/months/{month_id}/grid/compute", headers=ADMIN)
    assert compute.status_code == 200
    rows = client.get(f"/months/{month_id}/grid", headers=ADMIN)
    assert rows.status_code == 200
    body = rows.json()
    assert len(body) == 3
    for entry in body:
        assert entry["rule_pack_version"] == "mobiup-v1-compat"
        assert len(entry["inputs_hash"]) == 64
        assert len(entry["outputs_hash"]) == 64


def test_salary_upsert_admin_only(engine, faker_tenant, client):
    from ugrile.core import database
    with database.session_scope() as session:
        month_id = _open_month_with_calendar(client, faker_tenant, session)
    response = client.post(
        f"/months/{month_id}/salary",
        json={
            "person_id": faker_tenant["person_a_id"],
            "effective_from": "2026-01-01",
            "effective_to": None,
            "salary": "2600",
            "tickets": "480",
            "flip": "0",
            "source": "HR_MASTER",
            "notes": None,
        },
        headers=MANAGER,
    )
    assert response.status_code == 403
    response = client.post(
        f"/months/{month_id}/salary",
        json={
            "person_id": faker_tenant["person_a_id"],
            "effective_from": "2026-01-01",
            "effective_to": None,
            "salary": "2600",
            "tickets": "480",
            "flip": "0",
            "source": "HR_MASTER",
            "notes": None,
        },
        headers=ADMIN,
    )
    assert response.status_code == 200
    body = response.json()
    # Pydantic preserves the underlying Decimal precision: 2600 → "2600".
    assert body["salary"] in {"2600", "2600.00"}
    assert body["tickets"] in {"480", "480.00"}


def test_salary_get_lists_windows(engine, faker_tenant, client):
    from ugrile.core import database
    with database.session_scope() as session:
        month_id = _open_month_with_calendar(client, faker_tenant, session)
        from ugrile.repositories.salary import SalaryRepository
        SalaryRepository(session).upsert_window(
            tenant_id=faker_tenant["tenant_id"],
            person_id=faker_tenant["person_a_id"],
            effective_from=date(2026, 1, 1),
            effective_to=None,
            salary=Decimal("2600"),
            tickets=Decimal("480"),
            flip=Decimal("0"),
        )
        session.commit()
    response = client.get(f"/months/{month_id}/salary", headers=ADMIN)
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) >= 1
    assert any(row["person_id"] == faker_tenant["person_a_id"] for row in rows)


def test_attribution_endpoint_returns_projection(engine, faker_tenant, client):
    from ugrile.core import database
    with database.session_scope() as session:
        month_id = _open_month_with_calendar(client, faker_tenant, session)
    response = client.get(f"/months/{month_id}/attribution", headers=ADMIN)
    assert response.status_code == 200
    body = response.json()
    assert body["month_id"] == month_id
    assert body["company_total"] == "12500.00"
    assert body["total_rows"] >= 1
    for row in body["rows"]:
        assert row["amount"] in {"0.00", "12500.00"}
