from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook
from sqlalchemy import select

from ugrile.core import database
from ugrile.domain.enums import MonthState
from ugrile.repositories.models import AuditEvent, PersonDayAbsence, SiteDayAssignment
from ugrile.repositories.months import MonthRepository

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _closed_month_id(faker_tenant) -> str:
    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
        month.state = MonthState.CLOSED.value
        month.revision = 7
        session.commit()
        return month.id


def _assert_month_unchanged(faker_tenant, month_id: str) -> None:
    with database.session_scope() as session:
        month = MonthRepository(session).get(month_id)
        assert month.state == MonthState.CLOSED.value
        assert month.revision == 7
        assert (
            session.query(SiteDayAssignment)
            .filter_by(tenant_id=faker_tenant["tenant_id"], month_id=month_id)
            .count()
            == 0
        )
        assert (
            session.query(PersonDayAbsence)
            .filter_by(tenant_id=faker_tenant["tenant_id"], month_id=month_id)
            .count()
            == 0
        )
        audits = list(
            session.execute(
                select(AuditEvent).where(
                    AuditEvent.tenant_id == faker_tenant["tenant_id"],
                    AuditEvent.entity == "month",
                    AuditEvent.entity_id == month_id,
                    AuditEvent.action == "CALENDAR_APPLY",
                )
            ).scalars()
        )
        assert audits == []


def _assert_month_closed(response) -> None:
    assert response.status_code == 409, response.text
    assert response.json()["details"]["code"] == "MONTH_CLOSED"


def test_closed_month_rejects_assignment_compatibility_write(client, faker_tenant):
    month_id = _closed_month_id(faker_tenant)
    response = client.post(
        f"/months/{month_id}/assignments",
        headers=ADMIN,
        json={
            "month_id": month_id,
            "store_id": faker_tenant["store_id"],
            "person_id": faker_tenant["person_a_id"],
            "business_date": "2026-08-10",
            "working_kind": "NORMAL",
            "expected_revision": 7,
        },
    )
    _assert_month_closed(response)
    _assert_month_unchanged(faker_tenant, month_id)


def test_closed_month_rejects_calendar_apply(client, faker_tenant):
    month_id = _closed_month_id(faker_tenant)
    response = client.post(
        f"/months/{month_id}/calendar/apply",
        headers=ADMIN,
        json={
            "expected_revision": 7,
            "changes": [
                {
                    "person_id": faker_tenant["person_a_id"],
                    "business_date": "2026-08-11",
                    "status": "WORKING",
                    "store_id": faker_tenant["store_id"],
                    "working_kind": "NORMAL",
                }
            ],
        },
    )
    _assert_month_closed(response)
    _assert_month_unchanged(faker_tenant, month_id)


def test_closed_month_rejects_program_cell(client, faker_tenant):
    month_id = _closed_month_id(faker_tenant)
    response = client.post(
        f"/months/{month_id}/program/cell?expected_revision=7",
        headers=ADMIN,
        json={
            "person_id": faker_tenant["person_a_id"],
            "business_date": "2026-08-12",
            "status": "WORKING",
            "store_id": faker_tenant["store_id"],
            "working_kind": "NORMAL",
        },
    )
    _assert_month_closed(response)
    _assert_month_unchanged(faker_tenant, month_id)


def test_closed_month_rejects_xlsx_apply_without_consuming_business_state(client, faker_tenant):
    month_id = _closed_month_id(faker_tenant)
    template = client.get(f"/months/{month_id}/schedule/template", headers=ADMIN)
    assert template.status_code == 200, template.text

    workbook = load_workbook(BytesIO(template.content))
    workbook["user_admin"]["D2"] = "NORMAL - s1"
    edited = BytesIO()
    workbook.save(edited)

    response = client.post(
        f"/months/{month_id}/schedule/apply",
        headers=ADMIN,
        files={"file": ("schedule.xlsx", edited.getvalue(), XLSX)},
    )
    _assert_month_closed(response)
    _assert_month_unchanged(faker_tenant, month_id)
