from __future__ import annotations

import json
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

from ugrile.core import database
from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.repositories.models import AuditEvent
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}


def test_program_cell_records_authenticated_actor_and_route_source(
    engine, faker_tenant, client: TestClient
):
    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
        month.state = MonthState.OPEN
        CalendarService(session).apply(
            month=month,
            tenant_id=faker_tenant["tenant_id"],
            changes=[
                CalendarChange(
                    person_id=faker_tenant["person_a_id"],
                    business_date=date(2026, 8, 1),
                    store_id=faker_tenant["store_id"],
                    status=DayStatus.WORKING,
                    working_kind=WorkingKind.NORMAL,
                )
            ],
            expected_revision=month.revision,
            actor_id="fixture_setup",
            source="TEST_SETUP",
        )
        session.commit()
        month_id = month.id

    response = client.post(
        f"/months/{month_id}/program/cell?expected_revision=1",
        headers=ADMIN,
        json={
            "person_id": faker_tenant["person_b_id"],
            "business_date": "2026-08-02",
            "status": "WORKING",
            "store_id": faker_tenant["store_id"],
            "working_kind": "NORMAL",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2

    with database.session_scope() as session:
        event = session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.tenant_id == faker_tenant["tenant_id"],
                AuditEvent.entity == "month",
                AuditEvent.entity_id == month_id,
                AuditEvent.action == "CALENDAR_APPLY",
            )
            .order_by(AuditEvent.id.desc())
            .limit(1)
        ).scalar_one()
        payload = json.loads(event.payload)

    assert event.actor_id == "user_admin"
    assert payload["source"] == "API_PROGRAM_CELL"
    assert payload["revision_before"] == 1
    assert payload["revision_after"] == 2
    assert payload["correlation_id"]
    assert payload["changes"][0]["before"] is None
    assert payload["changes"][0]["after"]["person_id"] == faker_tenant["person_b_id"]
    assert payload["changes"][0]["after"]["status"] == "WORKING"
