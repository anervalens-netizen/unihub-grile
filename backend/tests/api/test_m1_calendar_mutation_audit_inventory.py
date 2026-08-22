from __future__ import annotations

import json
from datetime import date
from io import BytesIO

from openpyxl import load_workbook
from sqlalchemy import select

from ugrile.core import database
from ugrile.domain.enums import MonthState
from ugrile.repositories.models import AuditEvent
from ugrile.repositories.months import MonthRepository

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _open_month(faker_tenant) -> str:
    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
        month.state = MonthState.OPEN
        session.commit()
        return month.id


def _assert_single_route_audit(
    faker_tenant,
    *,
    month_id: str,
    source: str,
    person_id: str,
    business_date: str,
    expect_single_change: bool = True,
) -> None:
    with database.session_scope() as session:
        events = list(
            session.execute(
                select(AuditEvent).where(
                    AuditEvent.tenant_id == faker_tenant["tenant_id"],
                    AuditEvent.entity == "month",
                    AuditEvent.entity_id == month_id,
                    AuditEvent.action == "CALENDAR_APPLY",
                )
            ).scalars()
        )
    assert len(events) == 1
    event = events[0]
    payload = json.loads(event.payload)

    assert event.actor_id == "user_admin"
    assert payload["source"] == source
    assert payload["revision_before"] == 0
    assert payload["revision_after"] == 1
    assert payload["correlation_id"]
    changes = payload["changes"]
    if expect_single_change:
        assert len(changes) == 1

    target_changes = [
        change
        for change in changes
        if change["person_id"] == person_id and change["business_date"] == business_date
    ]
    assert len(target_changes) == 1
    target = target_changes[0]
    assert target["before"] is None
    assert target["after"]["person_id"] == person_id


def test_assignment_compatibility_write_is_audited(client, faker_tenant):
    month_id = _open_month(faker_tenant)
    business_date = "2026-08-10"
    response = client.post(
        f"/months/{month_id}/assignments",
        headers=ADMIN,
        json={
            "month_id": month_id,
            "store_id": faker_tenant["store_id"],
            "person_id": faker_tenant["person_a_id"],
            "business_date": business_date,
            "working_kind": "NORMAL",
            "expected_revision": 0,
        },
    )
    assert response.status_code == 200, response.text
    _assert_single_route_audit(
        faker_tenant,
        month_id=month_id,
        source="API_ASSIGNMENT",
        person_id=faker_tenant["person_a_id"],
        business_date=business_date,
    )


def test_calendar_apply_write_is_audited(client, faker_tenant):
    month_id = _open_month(faker_tenant)
    business_date = "2026-08-11"
    response = client.post(
        f"/months/{month_id}/calendar/apply",
        headers=ADMIN,
        json={
            "expected_revision": 0,
            "changes": [
                {
                    "person_id": faker_tenant["person_a_id"],
                    "business_date": business_date,
                    "status": "WORKING",
                    "store_id": faker_tenant["store_id"],
                    "working_kind": "NORMAL",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    _assert_single_route_audit(
        faker_tenant,
        month_id=month_id,
        source="API_CALENDAR",
        person_id=faker_tenant["person_a_id"],
        business_date=business_date,
    )


def test_program_cell_write_is_audited(client, faker_tenant):
    month_id = _open_month(faker_tenant)
    business_date = "2026-08-12"
    response = client.post(
        f"/months/{month_id}/program/cell?expected_revision=0",
        headers=ADMIN,
        json={
            "person_id": faker_tenant["person_a_id"],
            "business_date": business_date,
            "status": "WORKING",
            "store_id": faker_tenant["store_id"],
            "working_kind": "NORMAL",
        },
    )
    assert response.status_code == 200, response.text
    _assert_single_route_audit(
        faker_tenant,
        month_id=month_id,
        source="API_PROGRAM_CELL",
        person_id=faker_tenant["person_a_id"],
        business_date=business_date,
    )


def test_xlsx_apply_write_is_audited(client, faker_tenant):
    month_id = _open_month(faker_tenant)
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
    assert response.status_code == 200, response.text
    _assert_single_route_audit(
        faker_tenant,
        month_id=month_id,
        source="XLSX_IMPORT",
        person_id=faker_tenant["person_a_id"],
        business_date=date(2026, 8, 1).isoformat(),
        expect_single_change=False,
    )
