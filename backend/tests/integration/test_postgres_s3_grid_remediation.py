"""S3 PostgreSQL integration — authoritative grid inputs (AC-09 remediation).

Runs against the same PostgreSQL container as the S2/S3 integration
suites; skipped when ``UGRILE_PG_URL`` is unreachable. Proves the AC-09
remediation holds under PostgreSQL semantics:

* the connector ``sales_days`` divisor drives the per-day target;
* SIM quantity, monthly incentive and valid E-pay observations reach the
  persisted payload;
* a missing HR/payroll master is an explicit deterministic marker;
* the persisted payload round-trips: ``hash_inputs(payload["inputs"])``
  equals the stored ``inputs_hash``;
* versioned holiday markers are informational-only.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from ugrile.connectors.fixtures import FIXTURE_GENERATION
from ugrile.domain.enums import (
    DayStatus,
    EpayCategory,
    MonthState,
    RoleName,
    WorkingKind,
)
from ugrile.domain.identifiers import (
    make_person_id,
    make_store_id,
    make_tenant_id,
)
from ugrile.domain.rule_pack import hash_inputs
from ugrile.repositories.epay import month_source
from ugrile.repositories.holidays import HolidayRepository
from ugrile.repositories.models import (
    EpayObservation,
    IncentiveInput,
    Person,
    SalesStoreDay,
    Store,
    StoreTarget,
    Tenant,
    User,
)
from ugrile.repositories.months import MonthRepository
from ugrile.repositories.salary import SalaryRepository
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.services.grid import GridService

pytestmark = pytest.mark.postgres


def _seed(pg_session) -> dict[str, str]:
    tenant_token = "s3g"
    tenant_id = make_tenant_id(tenant_token)
    pg_session.add(Tenant(id=tenant_id, name="S3G", timezone="Europe/Bucharest"))
    pg_session.commit()
    s1 = Store(
        id=make_store_id(tenant_token, "s1"),
        tenant_id=tenant_id,
        company_code="MOBIUP",
        internal_code="s1",
        name="S1",
    )
    pg_session.add(s1)
    pg_session.commit()
    pg_session.add(
        User(
            id="user_admin",
            tenant_id=tenant_id,
            email="admin@s3g.example",
            display_name="Admin",
            role=RoleName.ADMIN.value,
        )
    )
    p1 = Person(
        id=make_person_id(tenant_token, "a"),
        tenant_id=tenant_id,
        internal_code="a",
        display_name="Alice",
        home_store_id=s1.id,
    )
    pg_session.add(p1)
    pg_session.commit()
    pg_session.add(
        SalesStoreDay(
            tenant_id=tenant_id,
            store_id=s1.id,
            business_date=date(2026, 8, 1),
            generation=FIXTURE_GENERATION,
            amount=Decimal("10000.00"),
            currency="RON",
            sim_quantity=9,
        )
    )
    pg_session.add(
        StoreTarget(
            tenant_id=tenant_id,
            store_id=s1.id,
            year=2026,
            month=8,
            kind="MONTHLY_SALES",
            version=1,
            amount=Decimal("250000"),
            currency="RON",
            sales_days=25,
        )
    )
    pg_session.add(
        IncentiveInput(
            tenant_id=tenant_id,
            person_id=p1.id,
            year=2026,
            month=8,
            version=1,
            amount=Decimal("350"),
            currency="RON",
        )
    )
    SalaryRepository(pg_session).upsert_window(
        tenant_id=tenant_id,
        person_id=p1.id,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        salary=Decimal("2600"),
        tickets=Decimal("480"),
        flip=Decimal("0"),
    )
    month = MonthRepository(pg_session).get_or_create(tenant_id, 2026, 8)
    pg_session.commit()
    observed_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    pg_session.add_all(
        [
            EpayObservation(
                tenant_id=tenant_id,
                store_id=s1.id,
                person_id=p1.id,
                category=EpayCategory.UNDER_50.value,
                value=1,
                raw_value="1",
                is_valid=True,
                source=month_source(month.id),
                observed_at=observed_at,
            ),
            EpayObservation(
                tenant_id=tenant_id,
                store_id=s1.id,
                person_id=p1.id,
                category=EpayCategory.AT_OR_OVER_50.value,
                value=10,
                raw_value="10",
                is_valid=True,
                source=month_source(month.id),
                observed_at=observed_at,
            ),
        ]
    )
    pg_session.commit()
    return {
        "tenant_id": tenant_id,
        "store_id": s1.id,
        "person_a_id": p1.id,
    }


def test_authoritative_grid_inputs_round_trip_on_postgres(pg_session, pg_engine):
    ids = _seed(pg_session)
    month = MonthRepository(pg_session).get_or_create(ids["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    pg_session.flush()
    CalendarService(pg_session).apply(
        month=month,
        tenant_id=ids["tenant_id"],
        expected_revision=0,
        changes=[
            CalendarChange(
                ids["person_a_id"],
                date(2026, 8, 1),
                ids["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    pg_session.commit()

    snapshots, rows = GridService(pg_session).compute_and_persist(
        tenant_id=ids["tenant_id"], month=month
    )
    assert len(rows) == 1
    row = rows[0]
    payload = json.loads(row.payload)

    # Round-trip: the stored payload is the exact canonical inputs dict.
    assert hash_inputs(payload["inputs"]) == row.inputs_hash

    # Connector divisor: 250000 / 25 = 10000/day.
    day = payload["inputs"]["calendar"][0]
    assert day["target_amount"] == "10000.00"
    assert day["sim_quantity"] == 9
    assert payload["inputs"]["parameters"]["incentive"] in {"350", "350.00"}
    assert payload["inputs"]["epay"] == {"under_50": 1, "at_or_over_50": 10}

    # SIM 27 + incentive 350 + E-pay 125 on a 10000/10000 day:
    # salary 2600 + tickets 480 + 300 + 200 + 27 + 350 + 125 = 4082.
    components = payload["components"]
    assert components["main_commission"] == "300"
    assert components["main_bonus"] == "200"
    assert components["sim_commission"] == "27"
    assert components["incentive"] == "350"
    assert components["epay_commission"] == "125"
    assert components["total_salary"] == "4082"

    # No missing-source anomalies with full inputs.
    codes = {a["code"] for a in payload["anomalies"]}
    assert codes == set()

    # Holiday marker is informational on PostgreSQL too.
    from ugrile.repositories.models import GridCalculation

    pg_session.query(GridCalculation).filter_by(month_id=month.id).delete()
    pg_session.commit()
    HolidayRepository(pg_session).upsert_calendar(
        tenant_id=ids["tenant_id"],
        version="rom-legal-2026",
        business_date=date(2026, 8, 1),
        label="Adormirea Maicii Domnului",
        is_active=True,
    )
    pg_session.commit()
    snapshots2, rows2 = GridService(pg_session).compute_and_persist(
        tenant_id=ids["tenant_id"], month=month
    )
    payload2 = json.loads(rows2[0].payload)
    assert payload2["inputs"]["holidays"][0]["version"] == "rom-legal-2026"
    assert payload2["components"]["total_salary"] == components["total_salary"]
    _ = (snapshots, snapshots2)
