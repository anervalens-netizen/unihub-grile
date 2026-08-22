"""S4 program grid tests — per magazine / per agenti calendar, cell edit."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from tests.conftest_helpers import fixture_for_tenant
from ugrile.connectors.ingest import FixtureConnector
from ugrile.core import database
from ugrile.domain.enums import DayStatus, MonthState, RoleName, WorkingKind
from ugrile.repositories.models import ManagerScope, User
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService

HEADERS = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}


@pytest.fixture()
def client(engine, faker_tenant):
    from ugrile.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def fixture_month_with_calendar(client, faker_tenant):
    with database.session_scope() as session:
        connector = FixtureConnector(session)
        connector.apply(fixture_for_tenant(faker_tenant["tenant_id"]))
        month = MonthRepository(session).get_or_create(
            faker_tenant["tenant_id"], 2026, 8
        )
        month.state = MonthState.OPEN
        session.commit()
        person_a = faker_tenant["person_a_id"]
        store_id = faker_tenant["store_id"]
        CalendarService(session).apply(
            month=month,
            tenant_id=faker_tenant["tenant_id"],
            changes=[
                CalendarChange(
                    person_id=person_a,
                    business_date=date(2026, 8, 1),
                    store_id=store_id,
                    status=DayStatus.WORKING,
                    working_kind=WorkingKind.NORMAL,
                ),
            ],
            expected_revision=month.revision,
        )
        session.commit()
        return month.id


def test_program_grid_per_magazinel_returns_one_row_per_store(client, fixture_month_with_calendar):
    response = client.get(
        f"/months/{fixture_month_with_calendar}/program?perspective=stores",
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["month_id"] == fixture_month_with_calendar
    assert len(body["dates"]) == 31
    # 2 stores from faker_tenant + 2 stores from the canonical fixture.
    assert len(body["rows"]) == 4
    for row in body["rows"]:
        assert len(row["cells"]) == 31


def test_program_grid_per_agenti_returns_one_row_per_person(client, fixture_month_with_calendar):
    response = client.get(
        f"/months/{fixture_month_with_calendar}/program?perspective=people",
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # 3 people from faker_tenant + 3 people from the canonical fixture.
    assert len(body["rows"]) == 6


def test_program_grid_marks_working_cell_badge(client, fixture_month_with_calendar, faker_tenant):
    response = client.get(
        f"/months/{fixture_month_with_calendar}/program?perspective=stores",
        headers=HEADERS,
    )
    body = response.json()
    person_a = faker_tenant["person_a_id"]
    store_id = faker_tenant["store_id"]
    store_row = next(row for row in body["rows"] if row["row_id"] == store_id)
    cells = {cell["business_date"]: cell for cell in store_row["cells"]}
    assert cells["2026-08-01"]["badge"] == "NORMAL"
    assert cells["2026-08-01"]["person_id"] == person_a


def test_program_cell_edit_round_trip(client, fixture_month_with_calendar, faker_tenant):
    payload = {
        "person_id": faker_tenant["person_b_id"],
        "business_date": "2026-08-02",
        "status": "WORKING",
        "store_id": faker_tenant["store_id"],
        "working_kind": "NORMAL",
    }
    response = client.post(
        f"/months/{fixture_month_with_calendar}/program/cell?expected_revision=1",
        json=payload,
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == 2


def test_program_cell_edit_stale_revision_returns_409(client, fixture_month_with_calendar):
    payload = {
        "person_id": "person_acme_a",
        "business_date": "2026-08-02",
        "status": "WORKING",
        "store_id": "store_acme_s1",
        "working_kind": "NORMAL",
    }
    response = client.post(
        f"/months/{fixture_month_with_calendar}/program/cell?expected_revision=99",
        json=payload,
        headers=HEADERS,
    )
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "STALE_REVISION"
    assert body["details"] == {"expected": 99, "current": 1}


def test_program_grid_invalid_perspective_rejected(client, fixture_month_with_calendar):
    response = client.get(
        f"/months/{fixture_month_with_calendar}/program?perspective=banana",
        headers=HEADERS,
    )
    assert response.status_code == 403


def test_program_grid_locked_cells_for_out_of_scope_tl(client, fixture_month_with_calendar, faker_tenant):
    with database.session_scope() as session:
        session.add(
            User(
                id="user_tl",
                tenant_id=faker_tenant["tenant_id"],
                email="tl@acme.example",
                display_name="TL",
                role=RoleName.MANAGER.value,
            )
        )
        # Scope TL only to other_store for days 1..15; day 16+ is out of scope.
        session.add(
            ManagerScope(
                tenant_id=faker_tenant["tenant_id"],
                user_id="user_tl",
                store_id=faker_tenant["other_store_id"],
                effective_from=date(2026, 8, 1),
                effective_to=date(2026, 8, 15),
            )
        )
        session.commit()
    headers = {"X-Ugrile-Identity": "user_tl", "X-Ugrile-Tenant": faker_tenant["tenant_id"]}
    response = client.get(
        f"/months/{fixture_month_with_calendar}/program?perspective=stores",
        headers=headers,
    )
    body = response.json()
    assert len(body["rows"]) == 1
    cells = body["rows"][0]["cells"]
    assert any(cell["locked"] is False for cell in cells[:15])
    assert all(cell["locked"] is True for cell in cells[15:])
