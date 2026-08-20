"""HTTP API tests using FastAPI TestClient + SQLite in-memory."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

import ugrile.repositories.models  # noqa: F401


@pytest.fixture()
def client(engine, faker_tenant):
    """Yield a TestClient wired to the in-memory engine seeded by faker_tenant."""

    from ugrile.main import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c


HEADERS = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}


def test_health(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] is True


def test_ready(client):
    r = client.get("/readyz")
    # SQLite has no alembic_version; readyz surfaces that.
    assert r.status_code == 200
    body = r.json()
    assert body["schema_version"] == "missing"


def test_create_and_conflict(client):
    # Seed a month first via direct DB call.
    from ugrile.core import database
    from ugrile.repositories.months import MonthRepository

    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create("tenant_acme", 2026, 8)
        session.commit()

    # Open the month so assignments are allowed.
    from ugrile.domain.enums import MonthState

    with database.session_scope() as session:
        m = MonthRepository(session).get(month.id)
        m.state = MonthState.OPEN
        session.commit()

    payload = {
        "month_id": month.id,
        "store_id": "store_s1",
        "person_id": "person_a",
        "business_date": date(2026, 8, 10).isoformat(),
        "working_kind": "NORMAL",
    }
    r = client.post(f"/months/{month.id}/assignments", json=payload, headers=HEADERS)
    assert r.status_code == 200, r.text

    payload2 = {
        "month_id": month.id,
        "store_id": "store_s1",
        "person_id": "person_b",
        "business_date": date(2026, 8, 10).isoformat(),
        "working_kind": "NORMAL",
    }
    r = client.post(f"/months/{month.id}/assignments", json=payload2, headers=HEADERS)
    assert r.status_code == 409
    body = r.json()
    assert body["detail"]["code"] == "COVERAGE_INVARIANT"


def test_ingest_fixture(client):
    r = client.post("/ingest/fixture", headers=HEADERS, json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stores"] == 2
    assert body["people"] == 3
    assert body["sales"] == 3
