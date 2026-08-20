"""S4 query-count assertion.

AC-10 requires the overview endpoint to be a *single aggregate request*,
not a per-store fan-out. The test wraps the session in an event listener
that counts SELECT statements and asserts the total stays below a small
constant, independent of the number of stores seeded.

The threshold is conservative: the overview needs
* one aggregate for stores,
* one aggregate for assignments (covered pairs),
* two aggregates for conflicts (store-day, person-day),
* two aggregates for extra days,
* one aggregate for sales,
* one aggregate for epay invalids,
* one aggregate per sync counter (3),
* one aggregate for grid snapshot rule_pack_version,
* the lattice validation (3 more aggregates for sales/targets).

= ~13 statements total, well under the AC-10 "no per-store fan-out"
contract.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from tests.conftest_helpers import fixture_for_tenant
from ugrile.connectors.ingest import FixtureConnector
from ugrile.core import database
from ugrile.domain.enums import MonthState
from ugrile.domain.identifiers import make_store_id
from ugrile.repositories.models import Store
from ugrile.repositories.months import MonthRepository

HEADERS = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}

OVERVIEW_SELECT_BUDGET = 25  # conservative ceiling
PROGRAM_SELECT_BUDGET = 25
EXCEPTIONS_SELECT_BUDGET = 25


@contextmanager
def _count_selects() -> Iterator[list[int]]:
    counts: list[int] = []
    engine = database._engine

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counts.append(1)

    event.listen(engine, "before_cursor_execute", _before_cursor_execute)
    try:
        yield counts
    finally:
        event.remove(engine, "before_cursor_execute", _before_cursor_execute)


@pytest.fixture()
def client(engine, faker_tenant):
    from ugrile.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def _seed_many_stores(session, tenant_id: str, count: int) -> None:
    """Seed extra stores so the assertion holds even at 75+ stores."""

    existing = session.query(Store).filter_by(tenant_id=tenant_id).count()
    for offset in range(existing, count):
        session.add(
            Store(
                id=make_store_id("acme", f"store-{offset:03d}"),
                tenant_id=tenant_id,
                company_code="MOBIUP",
                internal_code=f"s-{offset:03d}",
                name=f"Store {offset:03d}",
            )
        )
    session.commit()


def test_overview_single_aggregate_under_budget(client, faker_tenant):
    with database.session_scope() as session:
        connector = FixtureConnector(session)
        connector.apply(fixture_for_tenant(faker_tenant["tenant_id"]))
        _seed_many_stores(session, faker_tenant["tenant_id"], 80)
        month = MonthRepository(session).get_or_create(
            faker_tenant["tenant_id"], 2026, 8
        )
        month.state = MonthState.OPEN
        session.commit()
        month_id = month.id
    with _count_selects() as selects:
        response = client.get(f"/months/{month_id}/overview", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert len(selects) <= OVERVIEW_SELECT_BUDGET, (
        f"overview issued {len(selects)} SELECTs (> budget {OVERVIEW_SELECT_BUDGET}); "
        "AC-10 forbids per-store fan-out"
    )


def test_program_grid_single_aggregate_under_budget(client, faker_tenant):
    with database.session_scope() as session:
        connector = FixtureConnector(session)
        connector.apply(fixture_for_tenant(faker_tenant["tenant_id"]))
        _seed_many_stores(session, faker_tenant["tenant_id"], 80)
        month = MonthRepository(session).get_or_create(
            faker_tenant["tenant_id"], 2026, 8
        )
        month.state = MonthState.OPEN
        session.commit()
        month_id = month.id
    with _count_selects() as selects:
        response = client.get(
            f"/months/{month_id}/program?perspective=stores", headers=HEADERS
        )
    assert response.status_code == 200, response.text
    assert len(selects) <= PROGRAM_SELECT_BUDGET


def test_exceptions_single_aggregate_under_budget(client, faker_tenant):
    with database.session_scope() as session:
        connector = FixtureConnector(session)
        connector.apply(fixture_for_tenant(faker_tenant["tenant_id"]))
        _seed_many_stores(session, faker_tenant["tenant_id"], 80)
        month = MonthRepository(session).get_or_create(
            faker_tenant["tenant_id"], 2026, 8
        )
        month.state = MonthState.OPEN
        session.commit()
        month_id = month.id
    with _count_selects() as selects:
        response = client.get(f"/months/{month_id}/exceptions", headers=HEADERS)
    assert response.status_code == 200, response.text
    assert len(selects) <= EXCEPTIONS_SELECT_BUDGET
