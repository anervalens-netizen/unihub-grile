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


@pytest.fixture()
def worker_runner(engine):
    """Return a callable that drains the outbox exactly once.

    The contract says the API must not run jobs; tests use this helper to
    keep the single-authority invariant while still exercising the worker
    path synchronously. The runner lives in the same process as the API
    because both share the in-memory SQLite engine; the production
    equivalent is ``python -m ugrile.worker.worker``.
    """

    from ugrile.worker.worker import WORKER_LOCKED_BY, run_once

    def _run() -> object:
        row, result = run_once(locked_by=WORKER_LOCKED_BY)
        return row, result

    return _run


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


def test_create_and_conflict(client, faker_tenant):
    # Seed a month first via direct DB call.
    from ugrile.core import database
    from ugrile.repositories.months import MonthRepository

    tenant_id = faker_tenant["tenant_id"]
    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(tenant_id, 2026, 8)
        session.commit()

    # Open the month so assignments are allowed.
    from ugrile.domain.enums import MonthState

    with database.session_scope() as session:
        m = MonthRepository(session).get(month.id)
        m.state = MonthState.OPEN
        session.commit()

    payload = {
        "month_id": month.id,
        "store_id": faker_tenant["store_id"],
        "person_id": faker_tenant["person_a_id"],
        "business_date": date(2026, 8, 10).isoformat(),
        "working_kind": "NORMAL",
    }
    r = client.post(f"/months/{month.id}/assignments", json=payload, headers=HEADERS)
    assert r.status_code == 200, r.text

    payload2 = {
        "month_id": month.id,
        "store_id": faker_tenant["store_id"],
        "person_id": faker_tenant["person_b_id"],
        "business_date": date(2026, 8, 10).isoformat(),
        "working_kind": "NORMAL",
    }
    r = client.post(f"/months/{month.id}/assignments", json=payload2, headers=HEADERS)
    assert r.status_code == 409
    body = r.json()
    assert body["detail"]["code"] == "COVERAGE_INVARIANT"


def test_ingest_fixture_enqueues_job(client, worker_runner):
    """The API enqueues the fixture job; the worker settles it."""

    from ugrile.connectors.fixtures import FIXTURE_TENANT_TOKEN
    from ugrile.core import database
    from ugrile.domain.identifiers import make_tenant_id
    from ugrile.repositories.models import OutboxJob, StoreTarget

    tenant_id = make_tenant_id(FIXTURE_TENANT_TOKEN)
    with database.session_scope() as session:
        before = session.query(OutboxJob).count()

    r = client.post(
        "/ingest/fixture",
        headers=HEADERS,
        json={"tenant_token": FIXTURE_TENANT_TOKEN},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant"] == tenant_id
    assert body["generation"] == "FIXTURE_V1"

    with database.session_scope() as session:
        after = session.query(OutboxJob).count()
    assert after == before + 1

    # Drain the outbox once and assert the fixture landed under the
    # requested tenant.
    row, result = worker_runner()
    assert row is not None
    assert result is not None
    assert result.status == "DONE"

    with database.session_scope() as session:
        targets = session.query(StoreTarget).filter_by(tenant_id=tenant_id).count()
    assert targets >= 2
