"""S3 API tests — versioned holiday markers + admin overrides (Luna finding 4).

The endpoints are the minimal typed admin/read API for the holiday
calendar:

* ``GET /months/{id}/holidays`` — read markers (any same-tenant principal);
* ``POST /months/{id}/holidays`` — admin-only versioned legal date upsert;
* ``POST /months/{id}/holidays/override`` — admin-only override with reason.

The markers are informational: the grid consumes them as canonical inputs
without changing schedule, Pontaj, target or pay.
"""

from __future__ import annotations

from datetime import date

from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}
MANAGER = {"X-Ugrile-Identity": "user_manager", "X-Ugrile-Tenant": "tenant_acme"}


def _open_month(client, faker_tenant, session):
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
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
            )
        ],
    )
    session.commit()
    return month.id


def test_holiday_upsert_and_read_round_trip(engine, faker_tenant, client):
    from ugrile.core import database

    with database.session_scope() as session:
        month_id = _open_month(client, faker_tenant, session)

    # Upsert is admin-only.
    denied = client.post(
        f"/months/{month_id}/holidays",
        json={
            "version": "rom-legal-2026",
            "business_date": "2026-08-01",
            "label": "Adormirea Maicii Domnului",
            "is_active": True,
        },
        headers=MANAGER,
    )
    assert denied.status_code == 403

    created = client.post(
        f"/months/{month_id}/holidays",
        json={
            "version": "rom-legal-2026",
            "business_date": "2026-08-01",
            "label": "Adormirea Maicii Domnului",
            "is_active": True,
        },
        headers=ADMIN,
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["version"] == "rom-legal-2026"
    assert body["business_date"] == "2026-08-01"
    assert body["is_active"] is True
    assert body["override_active"] is None

    # Override is admin-only and audited with a reason.
    denied_override = client.post(
        f"/months/{month_id}/holidays/override",
        json={
            "version": "rom-legal-2026",
            "business_date": "2026-08-01",
            "is_active": True,
            "reason": "confirmat de admin",
        },
        headers=MANAGER,
    )
    assert denied_override.status_code == 403

    overridden = client.post(
        f"/months/{month_id}/holidays/override",
        json={
            "version": "rom-legal-2026",
            "business_date": "2026-08-01",
            "is_active": True,
            "reason": "confirmat de admin",
        },
        headers=ADMIN,
    )
    assert overridden.status_code == 200, overridden.text
    assert overridden.json()["override_active"] is True
    assert overridden.json()["override_reason"] == "confirmat de admin"

    # Read path returns the merged marker.
    read = client.get(f"/months/{month_id}/holidays", headers=MANAGER)
    assert read.status_code == 200
    markers = read.json()["markers"]
    assert len(markers) == 1
    marker = markers[0]
    assert marker["version"] == "rom-legal-2026"
    assert marker["business_date"] == "2026-08-01"
    assert marker["label"] == "Adormirea Maicii Domnului"
    assert marker["override_active"] is True
    assert marker["override_reason"] == "confirmat de admin"


def test_holiday_markers_land_in_grid_payload_without_pay_effect(
    engine, faker_tenant, client,
):
    from decimal import Decimal

    from ugrile.connectors.fixtures import FIXTURE_GENERATION
    from ugrile.core import database
    from ugrile.repositories.models import SalesStoreDay, StoreTarget

    with database.session_scope() as session:
        # Seed physical sales + target BEFORE the calendar apply so the
        # attribution projection captures the sale.
        session.add(
            SalesStoreDay(
                tenant_id=faker_tenant["tenant_id"],
                store_id=faker_tenant["store_id"],
                business_date=date(2026, 8, 1),
                generation=FIXTURE_GENERATION,
                amount=Decimal("10000"),
                currency="RON",
                sim_quantity=0,
            )
        )
        session.add(
            StoreTarget(
                tenant_id=faker_tenant["tenant_id"],
                store_id=faker_tenant["store_id"],
                year=2026,
                month=8,
                kind="MONTHLY_SALES",
                version=1,
                amount=Decimal("100000"),
                currency="RON",
                sales_days=20,
            )
        )
        session.commit()
        month_id = _open_month(client, faker_tenant, session)

    created = client.post(
        f"/months/{month_id}/holidays",
        json={
            "version": "rom-legal-2026",
            "business_date": "2026-08-01",
            "label": "Adormirea Maicii Domnului",
            "is_active": True,
        },
        headers=ADMIN,
    )
    assert created.status_code == 200

    compute = client.post(f"/months/{month_id}/grid/compute", headers=ADMIN)
    assert compute.status_code == 200, compute.text
    rows = compute.json()["snapshots"]
    alice = next(
        row
        for row in rows
        if row["person_id"] == faker_tenant["person_a_id"]
    )
    import json as _json

    payload = _json.loads(alice["payload"])
    holidays = payload["inputs"]["holidays"]
    assert len(holidays) == 1
    assert holidays[0]["version"] == "rom-legal-2026"
    assert holidays[0]["label"] == "Adormirea Maicii Domnului"
    # The marker is informational: the salary components are untouched.
    assert payload["components"]["main_commission"] == "300"
    assert payload["components"]["main_bonus"] == "400"
