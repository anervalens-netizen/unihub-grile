from __future__ import annotations

from datetime import date
from decimal import Decimal

from ugrile.connectors.fixtures import FIXTURE_GENERATION
from ugrile.core import database
from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.repositories.models import ManagerScope, SalesStoreDay
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService

MANAGER = {"X-Ugrile-Identity": "user_manager", "X-Ugrile-Tenant": "tenant_acme"}


def _sale(faker_tenant, *, day: int, amount: str) -> SalesStoreDay:
    return SalesStoreDay(
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
        business_date=date(2026, 8, day),
        generation=FIXTURE_GENERATION,
        amount=Decimal(amount),
        currency="RON",
    )


def test_attribution_scope_is_evaluated_per_business_date(client, faker_tenant):
    """Monthly store union must not expose rows/anomalies after scope expiry."""

    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
        month.state = MonthState.OPEN
        session.add(
            ManagerScope(
                tenant_id=faker_tenant["tenant_id"],
                user_id="user_manager",
                store_id=faker_tenant["store_id"],
                effective_from=date(2026, 8, 1),
                effective_to=date(2026, 8, 15),
            )
        )
        session.add_all(
            [
                _sale(faker_tenant, day=1, amount="100"),
                _sale(faker_tenant, day=20, amount="200"),
                # No worker on day 21: this produces an out-of-scope anomaly.
                _sale(faker_tenant, day=21, amount="300"),
            ]
        )
        session.commit()
        CalendarService(session).apply(
            month=month,
            tenant_id=faker_tenant["tenant_id"],
            expected_revision=0,
            changes=[
                CalendarChange(
                    faker_tenant["person_a_id"],
                    date(2026, 8, 1),
                    faker_tenant["store_id"],
                    DayStatus.WORKING,
                    WorkingKind.NORMAL,
                ),
                CalendarChange(
                    faker_tenant["person_a_id"],
                    date(2026, 8, 20),
                    faker_tenant["store_id"],
                    DayStatus.WORKING,
                    WorkingKind.NORMAL,
                ),
            ],
        )
        session.commit()
        month_id = month.id

    response = client.get(f"/months/{month_id}/attribution", headers=MANAGER)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_rows"] == 1
    assert body["company_total"] == "100.00"
    assert [(row["business_date"], row["amount"]) for row in body["rows"]] == [
        ("2026-08-01", "100.00")
    ]
    assert all(anomaly.get("business_date") != "2026-08-21" for anomaly in body["anomalies"])


def test_attribution_requires_person_effective_home_in_manager_scope(client, faker_tenant):
    """Working in a visible store does not expose a person owned by another store."""

    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
        month.state = MonthState.OPEN
        session.add(
            ManagerScope(
                tenant_id=faker_tenant["tenant_id"],
                user_id="user_manager",
                store_id=faker_tenant["store_id"],
                effective_from=date(2026, 8, 1),
                effective_to=date(2026, 8, 31),
            )
        )
        session.add_all(
            [
                _sale(faker_tenant, day=1, amount="100"),
                _sale(faker_tenant, day=2, amount="200"),
            ]
        )
        session.commit()
        CalendarService(session).apply(
            month=month,
            tenant_id=faker_tenant["tenant_id"],
            expected_revision=0,
            changes=[
                CalendarChange(
                    faker_tenant["person_a_id"],
                    date(2026, 8, 1),
                    faker_tenant["store_id"],
                    DayStatus.WORKING,
                    WorkingKind.NORMAL,
                ),
                CalendarChange(
                    faker_tenant["person_c_id"],
                    date(2026, 8, 2),
                    faker_tenant["store_id"],
                    DayStatus.WORKING,
                    WorkingKind.EXTRA_OTHER,
                ),
            ],
        )
        session.commit()
        month_id = month.id

    response = client.get(f"/months/{month_id}/attribution", headers=MANAGER)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_rows"] == 1
    assert body["company_total"] == "100.00"
    assert {row["person_id"] for row in body["rows"]} == {faker_tenant["person_a_id"]}
    assert faker_tenant["person_c_id"] not in {
        str(anomaly.get("person_id") or "") for anomaly in body["anomalies"]
    }
