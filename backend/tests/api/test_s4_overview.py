"""S4 overview endpoint tests.

The overview endpoint must:

* return a single aggregate payload (no per-store fan-out — verified by
  the query-count assertion in ``test_s4_query_count.py``);
* expose typed KPIs the manager UI can render without re-fetching;
* surface needs-attention entries sorted by severity;
* honour manager scope for both details and aggregate KPI values.

A typed ``STORE_DAY_UNCOVERED`` blocker is forced by leaving the fixture
month partly empty; the test asserts the overview reflects the exact
blocker count.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from tests.conftest_helpers import fixture_for_tenant
from ugrile.connectors.ingest import FixtureConnector
from ugrile.core import database
from ugrile.domain.enums import JobKind, MonthState, RoleName
from ugrile.repositories.models import EpayObservation, ManagerScope, Store, User
from ugrile.repositories.months import MonthRepository
from ugrile.worker.worker import enqueue

HEADERS = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}


@pytest.fixture()
def client(engine, faker_tenant):
    from ugrile.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture()
def fixture_month(client, faker_tenant):
    """Ingest the canonical fixture and open the month, return its id."""

    with database.session_scope() as session:
        connector = FixtureConnector(session)
        connector.apply(fixture_for_tenant(faker_tenant["tenant_id"]))
        month = MonthRepository(session).get_or_create(
            faker_tenant["tenant_id"], 2026, 8
        )
        month.state = MonthState.OPEN
        session.commit()
        return month.id


def test_overview_aggregate_renders_kpis_and_managers(client, fixture_month):
    response = client.get(f"/months/{fixture_month}/overview", headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["month_id"] == fixture_month
    assert body["state"] == "OPEN"
    assert body["revision"] == 0
    assert "kpis" in body and "managers" in body and "needs_attention" in body
    kpis = body["kpis"]
    # The fixture ships 2 stores + 3 sales rows; ``faker_tenant`` adds
    # 2 more. Coverage stays partial because the calendar was not populated.
    assert kpis["stores_total"] == 4
    assert kpis["stores_covered"] == 0
    assert kpis["days_uncovered"] >= 4 * 31  # 31 days per store
    assert kpis["extra_home_days"] == 0
    assert kpis["extra_other_days"] == 0


def test_overview_reports_blockers_sorted_by_severity(client, fixture_month):
    response = client.get(f"/months/{fixture_month}/overview", headers=HEADERS)
    body = response.json()
    assert body["needs_attention"], "expected uncovered-day blockers"
    severities = [item["severity"] for item in body["needs_attention"]]
    assert severities == sorted(severities), "needs-attention must be sorted by severity"
    codes = {item["code"] for item in body["needs_attention"]}
    assert "STORE_DAY_UNCOVERED" in codes


def test_overview_sheet_sync_counts_jobs(client, fixture_month, faker_tenant):
    """Enqueue two export jobs and assert the admin KPI surfaces them."""

    with database.session_scope() as session:
        for kind in [JobKind.EXPORT_XLSX_STORE, JobKind.GOOGLE_PROJECTION_STORE]:
            enqueue(
                session,
                tenant_id=faker_tenant["tenant_id"],
                kind=kind.value,
                idempotency_key=f"s4-test:{kind.value}:{id(kind)}",
            )
        session.commit()
    response = client.get(f"/months/{fixture_month}/overview", headers=HEADERS)
    body = response.json()
    kpis = body["kpis"]
    assert kpis["sheet_sync_total"] >= 2
    assert kpis["sheet_sync_stale"] >= 2


def test_overview_respects_manager_scope(engine, client, fixture_month, faker_tenant):
    """Aggregate KPIs and blocker rows must not leak out-of-scope resources."""

    with database.session_scope() as session:
        session.add(
            User(
                id="user_tl",
                tenant_id=faker_tenant["tenant_id"],
                email="tl@acme.example",
                display_name="TL",
                role=RoleName.MANAGER.value,
            )
        )
        stores = list(
            session.query(Store)
            .filter_by(tenant_id=faker_tenant["tenant_id"])
            .order_by(Store.id)
        )
        first_store, out_of_scope_store = stores[0], stores[1]
        session.add(
            ManagerScope(
                tenant_id=faker_tenant["tenant_id"],
                user_id="user_tl",
                store_id=first_store.id,
                effective_from=date(2026, 8, 1),
                effective_to=date(2026, 8, 31),
            )
        )
        # Seed one invalid E-pay row outside the TL scope. It must not affect
        # the manager KPI, even though the admin overview remains tenant-wide.
        session.add(
            EpayObservation(
                tenant_id=faker_tenant["tenant_id"],
                store_id=out_of_scope_store.id,
                person_id=faker_tenant["person_a_id"],
                category="UNDER_50",
                value=None,
                raw_value="bad",
                is_valid=False,
                source="TEST_SCOPE",
                observed_at=datetime(2026, 8, 15, 12, 0, tzinfo=UTC),
            )
        )
        # One visible and one invisible async job prove aggregate job counters
        # are authorized from persisted month/store metadata.
        enqueue(
            session,
            tenant_id=faker_tenant["tenant_id"],
            kind=JobKind.GOOGLE_PROJECTION_STORE.value,
            idempotency_key="scope-visible-job",
            payload={"month_id": fixture_month, "store_id": first_store.id},
        )
        enqueue(
            session,
            tenant_id=faker_tenant["tenant_id"],
            kind=JobKind.GOOGLE_PROJECTION_STORE.value,
            idempotency_key="scope-hidden-job",
            payload={"month_id": fixture_month, "store_id": out_of_scope_store.id},
        )
        session.commit()
        first_store_id = first_store.id
        hidden_store_id = out_of_scope_store.id

    headers = {
        "X-Ugrile-Identity": "user_tl",
        "X-Ugrile-Tenant": faker_tenant["tenant_id"],
    }
    response = client.get(f"/months/{fixture_month}/overview", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    kpis = body["kpis"]
    assert kpis["stores_total"] == 1
    assert kpis["stores_covered"] == 0
    assert kpis["days_uncovered"] == 31
    assert kpis["epay_invalid"] == 0
    assert kpis["sheet_sync_total"] == 1
    assert kpis["sheet_sync_stale"] == 1
    assert all(item["store_id"] != hidden_store_id for item in body["needs_attention"])
    assert any(item["store_id"] == first_store_id for item in body["needs_attention"])

    exceptions = client.get(f"/months/{fixture_month}/exceptions", headers=headers)
    assert exceptions.status_code == 200, exceptions.text
    assert all(item["store_id"] != hidden_store_id for item in exceptions.json())


def test_overview_unknown_month_returns_404(client):
    response = client.get("/months/month_unknown_2099-01/overview", headers=HEADERS)
    assert response.status_code == 404
