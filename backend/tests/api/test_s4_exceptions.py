"""S4 exceptions endpoint tests — typed list sorted by severity."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest_helpers import fixture_for_tenant
from ugrile.connectors.ingest import FixtureConnector
from ugrile.core import database
from ugrile.domain.enums import MonthState
from ugrile.repositories.months import MonthRepository

HEADERS = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}


@pytest.fixture()
def client(engine, faker_tenant):
    from ugrile.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def fixture_month_empty(client, faker_tenant):
    """Ingest fixture, open the month but leave the calendar empty so the
    full lattice produces uncovered-day blockers."""

    with database.session_scope() as session:
        connector = FixtureConnector(session)
        connector.apply(fixture_for_tenant(faker_tenant["tenant_id"]))
        month = MonthRepository(session).get_or_create(
            faker_tenant["tenant_id"], 2026, 8
        )
        month.state = MonthState.OPEN
        session.commit()
        return month.id


def test_exceptions_returns_typed_list_sorted_by_severity(client, fixture_month_empty):
    response = client.get(f"/months/{fixture_month_empty}/exceptions", headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body, "expected at least one blocker exception"
    severities = [item["severity"] for item in body]
    assert severities == sorted(severities)
    for item in body:
        assert item["code"]
        assert item["title"]
        assert item["action_hint"]
        assert item["blocking_close"] is True


def test_exceptions_includes_store_day_uncovered_for_full_lattice(client, fixture_month_empty):
    response = client.get(f"/months/{fixture_month_empty}/exceptions", headers=HEADERS)
    body = response.json()
    codes = {item["code"] for item in body}
    assert "STORE_DAY_UNCOVERED" in codes
    assert "SALES_MISSING_FOR_WORKED_DAY" in codes


def test_exceptions_are_tenant_scoped(engine, client, fixture_month_empty, faker_tenant):
    headers_other = {
        "X-Ugrile-Identity": "user_admin",
        "X-Ugrile-Tenant": faker_tenant["tenant_id"],
    }
    response = client.get(f"/months/{fixture_month_empty}/exceptions", headers=headers_other)
    assert response.status_code == 200
