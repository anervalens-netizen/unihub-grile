"""API tests for grid, payroll master and attribution authorization."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ugrile.connectors.fixtures import FIXTURE_GENERATION
from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.repositories.models import ManagerScope, SalesStoreDay, StoreTarget
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}
MANAGER = {"X-Ugrile-Identity": "user_manager", "X-Ugrile-Tenant": "tenant_acme"}


def _open_month_with_calendar(client, faker_tenant, session):
    _ = client
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    session.add(
        SalesStoreDay(
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            business_date=date(2026, 8, 1),
            generation=FIXTURE_GENERATION,
            amount=Decimal("12500"),
            currency="RON",
        )
    )
    session.add(
        StoreTarget(
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            year=2026,
            month=8,
            kind="MONTHLY_SALES",
            version=1,
            amount=Decimal("250000"),
            currency="RON",
        )
    )
    session.commit()
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
    return month.id


def _salary_payload(faker_tenant):
    return {
        "person_id": faker_tenant["person_a_id"],
        "effective_from": "2026-01-01",
        "effective_to": None,
        "salary": "2600",
        "tickets": "480",
        "flip": "0",
        "source": "HR_MASTER",
        "notes": None,
    }


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


def test_grid_get_returns_latest_persisted_rows(engine, faker_tenant, client):
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
        assert entry["revision"] == 1
        assert entry["rule_pack_version"] == "mobiup-v1-compat"
        assert len(entry["inputs_hash"]) == 64
        assert len(entry["outputs_hash"]) == 64


def test_financial_reads_survive_close_state_revision_bump(engine, faker_tenant, client):
    """CLOSE increments Month.revision but must not hide the just-approved snapshots."""

    from ugrile.core import database

    with database.session_scope() as session:
        month_id = _open_month_with_calendar(client, faker_tenant, session)
    compute = client.post(f"/months/{month_id}/grid/compute", headers=ADMIN)
    assert compute.status_code == 200

    with database.session_scope() as session:
        month = MonthRepository(session).get(month_id)
        assert month.revision == 1
        month.state = MonthState.CLOSED
        month.revision = 2
        session.commit()

    grid = client.get(f"/months/{month_id}/grid", headers=ADMIN)
    assert grid.status_code == 200, grid.text
    assert grid.json()
    assert {row["revision"] for row in grid.json()} == {1}

    attribution = client.get(f"/months/{month_id}/attribution", headers=ADMIN)
    assert attribution.status_code == 200, attribution.text
    assert attribution.json()["revision"] == 1
    assert attribution.json()["company_total"] == "12500.00"


def test_salary_upsert_admin_only(engine, faker_tenant, client):
    from ugrile.core import database

    with database.session_scope() as session:
        month_id = _open_month_with_calendar(client, faker_tenant, session)
    response = client.post(
        f"/months/{month_id}/salary",
        json=_salary_payload(faker_tenant),
        headers=MANAGER,
    )
    assert response.status_code == 403
    response = client.post(
        f"/months/{month_id}/salary",
        json=_salary_payload(faker_tenant),
        headers=ADMIN,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["salary"] in {"2600", "2600.00"}
    assert body["tickets"] in {"480", "480.00"}


def test_salary_master_read_is_not_exposed_to_manager(engine, faker_tenant, client):
    from ugrile.core import database
    from ugrile.repositories.salary import SalaryRepository

    with database.session_scope() as session:
        month_id = _open_month_with_calendar(client, faker_tenant, session)
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

    denied = client.get(f"/months/{month_id}/salary", headers=MANAGER)
    assert denied.status_code == 403
    allowed = client.get(f"/months/{month_id}/salary", headers=ADMIN)
    assert allowed.status_code == 200
    assert any(
        row["person_id"] == faker_tenant["person_a_id"] for row in allowed.json()
    )


def test_salary_master_write_is_frozen_after_close(engine, faker_tenant, client):
    from ugrile.core import database

    with database.session_scope() as session:
        month_id = _open_month_with_calendar(client, faker_tenant, session)
        month = MonthRepository(session).get(month_id)
        month.state = MonthState.CLOSED
        session.commit()

    response = client.post(
        f"/months/{month_id}/salary",
        json=_salary_payload(faker_tenant),
        headers=ADMIN,
    )
    assert response.status_code == 409, response.text
    assert response.json()["details"]["code"] == "MONTH_CLOSED"


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


def test_attribution_aggregate_and_rows_share_manager_scope(engine, faker_tenant, client):
    """A manager must not infer out-of-scope sales from tenant-wide aggregates."""

    from ugrile.core import database

    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
        month.state = MonthState.OPEN
        session.add(
            ManagerScope(
                tenant_id=faker_tenant["tenant_id"],
                user_id="user_manager",
                store_id=faker_tenant["store_id"],
                effective_from=date(2026, 8, 1),
                effective_to=date(2026, 8, 31),
            )
        )
        session.add_all(
            [
                SalesStoreDay(
                    tenant_id=faker_tenant["tenant_id"],
                    store_id=faker_tenant["store_id"],
                    business_date=date(2026, 8, 1),
                    generation=FIXTURE_GENERATION,
                    amount=Decimal("100"),
                    currency="RON",
                ),
                SalesStoreDay(
                    tenant_id=faker_tenant["tenant_id"],
                    store_id=faker_tenant["other_store_id"],
                    business_date=date(2026, 8, 1),
                    generation=FIXTURE_GENERATION,
                    amount=Decimal("900"),
                    currency="RON",
                ),
            ]
        )
        session.commit()
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
        month_id = month.id

    response = client.get(f"/months/{month_id}/attribution", headers=MANAGER)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_rows"] == 1
    assert body["company_total"] == "100.00"
    assert {row["store_id"] for row in body["rows"]} == {faker_tenant["store_id"]}

    denied = client.get(
        f"/months/{month_id}/attribution?store_id={faker_tenant['other_store_id']}",
        headers=MANAGER,
    )
    assert denied.status_code == 403
