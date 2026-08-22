from __future__ import annotations

from ugrile.core import database
from ugrile.domain.enums import RoleName
from ugrile.repositories.models import User
from ugrile.repositories.months import MonthRepository

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}
MANAGER = {"X-Ugrile-Identity": "user_manager", "X-Ugrile-Tenant": "tenant_acme"}
READONLY = {"X-Ugrile-Identity": "user_readonly", "X-Ugrile-Tenant": "tenant_acme"}


def _month_id(faker_tenant) -> str:
    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
        session.commit()
        return month.id


def _add_readonly(faker_tenant) -> None:
    with database.session_scope() as session:
        session.add(
            User(
                id="user_readonly",
                tenant_id=faker_tenant["tenant_id"],
                email="readonly@acme.example",
                display_name="Readonly",
                role=RoleName.READONLY.value,
            )
        )
        session.commit()


def test_close_checklist_and_events_are_not_manager_or_readonly_reads(client, faker_tenant):
    month_id = _month_id(faker_tenant)
    _add_readonly(faker_tenant)

    for headers in (MANAGER, READONLY):
        checklist = client.get(f"/months/{month_id}/close-checklist", headers=headers)
        assert checklist.status_code == 403, checklist.text

        events = client.get(f"/months/{month_id}/close-events", headers=headers)
        assert events.status_code == 403, events.text


def test_admin_retains_close_checklist_and_audit_reads(client, faker_tenant):
    month_id = _month_id(faker_tenant)

    checklist = client.get(f"/months/{month_id}/close-checklist", headers=ADMIN)
    assert checklist.status_code == 200, checklist.text
    assert checklist.json()["month_id"] == month_id

    events = client.get(f"/months/{month_id}/close-events", headers=ADMIN)
    assert events.status_code == 200, events.text
    assert events.json() == []
