"""S4 close checklist + reopen tests.

The Close page exposes:

* the typed blocker list;
* the exact ``expected_revision`` for the close action;
* the audit timeline (existing ``/months/{id}/close-events``);
* the reopen reason validation (>= 4 chars, admin-only).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest_helpers import fixture_for_tenant
from ugrile.connectors.ingest import FixtureConnector
from ugrile.core import database
from ugrile.domain.enums import MonthState, RoleName
from ugrile.repositories.models import User
from ugrile.repositories.months import MonthRepository

HEADERS = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}


@pytest.fixture()
def client(engine, faker_tenant):
    from ugrile.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def fixture_month_open(client, faker_tenant):
    with database.session_scope() as session:
        connector = FixtureConnector(session)
        connector.apply(fixture_for_tenant(faker_tenant["tenant_id"]))
        month = MonthRepository(session).get_or_create(
            faker_tenant["tenant_id"], 2026, 8
        )
        month.state = MonthState.OPEN
        session.commit()
        return month.id


def test_close_checklist_returns_blockers_and_revision(client, fixture_month_open):
    response = client.get(f"/months/{fixture_month_open}/close-checklist", headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "OPEN"
    assert body["revision"] >= 0
    assert isinstance(body["expected_revision"], int)
    assert body["blockers"], "expected at least one blocker"
    for item in body["blockers"]:
        assert item["code"]
        assert item["blocking"] is True


def test_close_checklist_sorted_by_severity(client, fixture_month_open):
    response = client.get(f"/months/{fixture_month_open}/close-checklist", headers=HEADERS)
    body = response.json()
    severities = [item["severity"] for item in body["blockers"]]
    assert severities == sorted(severities)


def test_reopen_requires_admin_role(engine, client, fixture_month_open, faker_tenant):
    """A manager attempting reopen must receive 403."""

    with database.session_scope() as session:
        session.add(
            User(
                id="user_mgr",
                tenant_id=faker_tenant["tenant_id"],
                email="mgr@acme.example",
                display_name="Mgr",
                role=RoleName.MANAGER.value,
            )
        )
        session.commit()
    response = client.post(
        f"/months/{fixture_month_open}/reopen-admin",
        json={"reason": "valid reason"},
        headers={"X-Ugrile-Identity": "user_mgr", "X-Ugrile-Tenant": faker_tenant["tenant_id"]},
    )
    assert response.status_code == 403
    body = response.json()
    # The ``ScopeError`` handler returns ``code/message/details`` at the
    # top level (no ``detail`` wrapper) so the manager UI can show the
    # precise reason.
    assert body["code"] == "FORBIDDEN"
    assert "admin" in body["message"].lower()


def test_reopen_admin_requires_reason_at_least_four_chars(client, fixture_month_open):
    response = client.post(
        f"/months/{fixture_month_open}/reopen-admin",
        json={"reason": "abc"},
        headers=HEADERS,
    )
    assert response.status_code == 422  # pydantic validation


def test_reopen_admin_succeeds_with_valid_reason(client, fixture_month_open, faker_tenant):
    with database.session_scope() as session:
        month = MonthRepository(session).get(fixture_month_open)
        month.state = MonthState.CLOSED
        session.commit()
    response = client.post(
        f"/months/{fixture_month_open}/reopen-admin",
        json={"reason": "corecție manuală omisă"},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "REOPENED"
    assert "blockers" in body
