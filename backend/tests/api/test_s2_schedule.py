"""S2 calendar and XLSX schedule API tests."""

from __future__ import annotations

from datetime import date
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from ugrile.core import database
from ugrile.domain.enums import DayStatus, MonthState, RoleName, WorkingKind
from ugrile.repositories.models import (
    ManagerScope,
    PersonDayAbsence,
    SiteDayAssignment,
    User,
)
from ugrile.repositories.months import MonthRepository

HEADERS = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture()
def client(engine, faker_tenant):
    from ugrile.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def _open_month(faker_tenant):
    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
        month.state = MonthState.OPEN
        session.commit()
        return month.id


def test_calendar_apply_derives_absence_and_revision(client, faker_tenant):
    month_id = _open_month(faker_tenant)
    payload = {
        "expected_revision": 0,
        "changes": [
            {
                "person_id": faker_tenant["person_a_id"],
                "business_date": "2026-08-01",
                "status": "WORKING",
                "store_id": faker_tenant["store_id"],
                "working_kind": "NORMAL",
            },
            {
                "person_id": faker_tenant["person_b_id"],
                "business_date": "2026-08-01",
                "status": "LEAVE",
            },
            {
                "person_id": faker_tenant["person_c_id"],
                "business_date": "2026-08-01",
                "status": "OFF",
            },
        ],
    }
    response = client.post(f"/months/{month_id}/calendar/apply", json=payload, headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == 1
    assert body["assignment_count"] == 1
    assert body["person_calendar_count"] == 3
    assert body["pontaj_count"] == 3

    with database.session_scope() as session:
        absences = session.query(PersonDayAbsence).filter_by(month_id=month_id).all()
        assert {(row.person_id, row.status) for row in absences} == {
            (faker_tenant["person_b_id"], DayStatus.LEAVE.value),
            (faker_tenant["person_c_id"], DayStatus.OFF.value),
        }
        assignments = session.query(SiteDayAssignment).filter_by(month_id=month_id).all()
        assert len(assignments) == 1

    stale = client.post(
        f"/months/{month_id}/calendar/apply",
        json={"expected_revision": 0, "changes": []},
        headers=HEADERS,
    )
    assert stale.status_code == 409


def test_xlsx_template_preview_and_atomic_apply(client, faker_tenant):
    month_id = _open_month(faker_tenant)
    template = client.get(f"/months/{month_id}/schedule/template", headers=HEADERS)
    assert template.status_code == 200, template.text
    assert template.headers["content-type"].startswith(XLSX)

    workbook = load_workbook(BytesIO(template.content))
    manager_sheet = workbook["user_admin"]
    manager_sheet["D2"] = "NORMAL - s1"
    edited = BytesIO()
    workbook.save(edited)
    edited_bytes = edited.getvalue()

    preview = client.post(
        f"/months/{month_id}/schedule/preview",
        files={"file": ("schedule.xlsx", edited_bytes, XLSX)},
        headers=HEADERS,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["errors"] == []
    assert preview.json()["base_revision"] == 0

    applied = client.post(
        f"/months/{month_id}/schedule/apply",
        files={"file": ("schedule.xlsx", edited_bytes, XLSX)},
        headers=HEADERS,
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["revision"] == 1

    with database.session_scope() as session:
        assignments = session.query(SiteDayAssignment).filter_by(month_id=month_id).all()
        assert len(assignments) == 1
        assert assignments[0].person_id == faker_tenant["person_a_id"]

    refreshed = client.get(f"/months/{month_id}/schedule/template", headers=HEADERS)
    assert refreshed.status_code == 200
    refreshed_workbook = load_workbook(BytesIO(refreshed.content), data_only=True)
    assert refreshed_workbook["user_admin"]["D2"].value == "NORMAL - s1"

    stale_apply = client.post(
        f"/months/{month_id}/schedule/apply",
        files={"file": ("schedule.xlsx", edited_bytes, XLSX)},
        headers=HEADERS,
    )
    assert stale_apply.status_code == 409
    assert stale_apply.json()["code"] == "STALE_REVISION"


def test_xlsx_preview_reports_coverage_and_kind_errors(client, faker_tenant):
    month_id = _open_month(faker_tenant)
    template = client.get(f"/months/{month_id}/schedule/template", headers=HEADERS)
    workbook = load_workbook(BytesIO(template.content))
    sheet = workbook["user_admin"]
    sheet["D2"] = "NORMAL - s1"
    sheet["D3"] = "NORMAL - s1"
    sheet["D4"] = "LIBER"
    edited = BytesIO()
    workbook.save(edited)

    conflict_preview = client.post(
        f"/months/{month_id}/schedule/preview",
        files={"file": ("schedule.xlsx", edited.getvalue(), XLSX)},
        headers=HEADERS,
    )
    assert conflict_preview.status_code == 200, conflict_preview.text
    conflict_codes = {error["code"] for error in conflict_preview.json()["errors"]}
    assert "COVERAGE_INVARIANT" in conflict_codes

    sheet["D2"] = "LIBER"
    sheet["D3"] = "LIBER"
    sheet["D4"] = "NORMAL - s1"
    invalid = BytesIO()
    workbook.save(invalid)
    invalid_preview = client.post(
        f"/months/{month_id}/schedule/preview",
        files={"file": ("schedule.xlsx", invalid.getvalue(), XLSX)},
        headers=HEADERS,
    )
    assert invalid_preview.status_code == 200, invalid_preview.text
    invalid_codes = {error["code"] for error in invalid_preview.json()["errors"]}
    assert "VALIDATION_ERROR" in invalid_codes


def test_xlsx_preview_reports_unknown_person_without_writes(client, faker_tenant):
    month_id = _open_month(faker_tenant)
    template = client.get(f"/months/{month_id}/schedule/template", headers=HEADERS)
    workbook = load_workbook(BytesIO(template.content))
    workbook["user_admin"]["A2"] = "person_acme_unknown"
    edited = BytesIO()
    workbook.save(edited)

    preview = client.post(
        f"/months/{month_id}/schedule/preview",
        files={"file": ("schedule.xlsx", edited.getvalue(), XLSX)},
        headers=HEADERS,
    )
    assert preview.status_code == 200
    assert any(error["code"] == "UNKNOWN_PERSON" for error in preview.json()["errors"])
    with database.session_scope() as session:
        assert session.query(SiteDayAssignment).filter_by(month_id=month_id).count() == 0


def test_manager_read_scope_filters_catalog_and_calendar(client, faker_tenant):
    month_id = _open_month(faker_tenant)
    manager_headers = {
        "X-Ugrile-Identity": "user_manager",
        "X-Ugrile-Tenant": "tenant_acme",
    }
    with database.session_scope() as session:
        manager = User(
            id="user_manager",
            tenant_id=faker_tenant["tenant_id"],
            email="manager@acme.example",
            display_name="Manager",
            role=RoleName.MANAGER.value,
        )
        session.add(manager)
        session.add(
            ManagerScope(
                tenant_id=faker_tenant["tenant_id"],
                user_id=manager.id,
                store_id=faker_tenant["store_id"],
                effective_from=date(2026, 8, 1),
            )
        )
        session.add_all(
            [
                SiteDayAssignment(
                    tenant_id=faker_tenant["tenant_id"],
                    month_id=month_id,
                    store_id=faker_tenant["store_id"],
                    person_id=faker_tenant["person_a_id"],
                    business_date=date(2026, 8, 1),
                    status=DayStatus.WORKING.value,
                    working_kind=WorkingKind.NORMAL.value,
                    revision=0,
                    source="TEST",
                ),
                SiteDayAssignment(
                    tenant_id=faker_tenant["tenant_id"],
                    month_id=month_id,
                    store_id=faker_tenant["other_store_id"],
                    person_id=faker_tenant["person_c_id"],
                    business_date=date(2026, 8, 1),
                    status=DayStatus.WORKING.value,
                    working_kind=WorkingKind.NORMAL.value,
                    revision=0,
                    source="TEST",
                ),
            ]
        )
        session.commit()

    assignments = client.get(f"/months/{month_id}/assignments", headers=manager_headers)
    assert assignments.status_code == 200, assignments.text
    assert [row["person_id"] for row in assignments.json()] == [faker_tenant["person_a_id"]]

    stores = client.get("/catalog/stores", headers=manager_headers)
    assert stores.status_code == 200, stores.text
    assert [row["id"] for row in stores.json()] == [faker_tenant["store_id"]]

    people = client.get("/catalog/people", headers=manager_headers)
    assert people.status_code == 200, people.text
    assert {row["id"] for row in people.json()} == {
        faker_tenant["person_a_id"],
        faker_tenant["person_b_id"],
    }
