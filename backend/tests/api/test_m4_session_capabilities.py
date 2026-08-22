from __future__ import annotations

from fastapi.testclient import TestClient

from ugrile.core import database
from ugrile.domain.enums import RoleName
from ugrile.repositories.models import User
from ugrile.services.authorization import Capability


def _headers(user_id: str) -> dict[str, str]:
    return {"X-Ugrile-Identity": user_id, "X-Ugrile-Tenant": "tenant_acme"}


def _seed_role_users(faker_tenant) -> None:
    with database.session_scope() as session:
        session.add_all(
            [
                User(
                    id="user_manager_session",
                    tenant_id=faker_tenant["tenant_id"],
                    email="manager-session@acme.example",
                    display_name="Manager Session",
                    role=RoleName.MANAGER.value,
                ),
                User(
                    id="user_readonly_session",
                    tenant_id=faker_tenant["tenant_id"],
                    email="readonly-session@acme.example",
                    display_name="Readonly Session",
                    role=RoleName.READONLY.value,
                ),
            ]
        )
        session.commit()


def test_admin_session_exposes_complete_capability_set(engine, faker_tenant, client: TestClient):
    response = client.get("/session", headers=_headers("user_admin"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user_id"] == "user_admin"
    assert body["tenant_id"] == faker_tenant["tenant_id"]
    assert body["role"] == "ADMIN"
    assert body["email"] == "admin@acme.example"
    assert body["capabilities"] == sorted(capability.value for capability in Capability)


def test_manager_and_readonly_session_capabilities_are_finite_server_policy(
    engine,
    faker_tenant,
    client: TestClient,
):
    _seed_role_users(faker_tenant)

    manager = client.get("/session", headers=_headers("user_manager_session"))
    readonly = client.get("/session", headers=_headers("user_readonly_session"))
    assert manager.status_code == 200, manager.text
    assert readonly.status_code == 200, readonly.text

    manager_capabilities = set(manager.json()["capabilities"])
    assert manager_capabilities == {
        "catalog.read",
        "schedule.read",
        "schedule.write",
        "grid.read",
        "holiday.read",
        "epay.read",
        "sheet.read",
        "export.read",
        "month.read",
        "jobs.read",
    }
    assert "sheet.sync" not in manager_capabilities
    assert "export.create" not in manager_capabilities
    assert "month.close.read" not in manager_capabilities

    assert readonly.json()["capabilities"] == [
        "catalog.read",
        "holiday.read",
        "month.read",
    ]
