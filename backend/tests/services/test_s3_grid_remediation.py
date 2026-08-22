"""S3 AC-09 remediation — authoritative grid inputs (Luna findings 1-5).

Focused tests that fail on the pre-remediation defects:

1. ``test_connector_sales_days_divides_monthly_target`` — the per-day
   target must use the connector ``sales_days`` divisor, not the calendar
   month length (docs/MOBIUP_RULE_PACK.md §2).
2. ``test_sim_incentive_and_epay_inputs_flow_into_snapshot`` — SIM
   quantity, monthly incentive and valid E-pay observations must reach the
   persisted payload instead of hardcoded zeroes (§5).
3. ``test_missing_salary_master_is_an_explicit_anomaly`` — a missing
   effective-dated HR/payroll row must be a deterministic marker, not a
   silent zero (§6).
4. ``test_persisted_payload_round_trips_to_inputs_hash`` — the stored
   payload must be the *actual* canonical inputs (``hash_inputs`` of
   ``payload["inputs"]`` equals ``inputs_hash``), not a synthetic one-day
   zero row.
5. ``test_holiday_marker_is_informational_only`` — versioned Romanian
   holiday markers + admin overrides land in the canonical inputs but never
   change schedule, Pontaj, target or pay (§9).
6. Connector ingest round-trip for the new v1 fields.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal

from ugrile.connectors.fixtures import FIXTURE_GENERATION
from ugrile.domain.enums import DayStatus, EpayCategory, MonthState, WorkingKind
from ugrile.domain.grid import GridAnomalyCode
from ugrile.domain.rule_pack import RULE_PACK_VERSION, hash_inputs
from ugrile.repositories.epay import latest_snapshot, month_source
from ugrile.repositories.holidays import HolidayRepository
from ugrile.repositories.models import (
    EpayObservation,
    GridCalculation,
    IncentiveInput,
    SalesStoreDay,
    StoreTarget,
)
from ugrile.repositories.months import MonthRepository
from ugrile.repositories.salary import SalaryRepository
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.services.grid import GridService


def _seed_store_target(
    session,
    faker_tenant,
    store_id: str,
    amount: str,
    sales_days: int | None,
    year: int = 2026,
    month: int = 8,
) -> None:
    session.add(
        StoreTarget(
            tenant_id=faker_tenant["tenant_id"],
            store_id=store_id,
            year=year,
            month=month,
            kind="MONTHLY_SALES",
            version=1,
            amount=Decimal(amount),
            currency="RON",
            sales_days=sales_days,
        )
    )
    session.commit()


def _seed_sale(
    session,
    faker_tenant,
    store_id: str,
    business_date: date,
    amount: str,
    sim_quantity: int = 0,
) -> None:
    session.add(
        SalesStoreDay(
            tenant_id=faker_tenant["tenant_id"],
            store_id=store_id,
            business_date=business_date,
            generation=FIXTURE_GENERATION,
            amount=Decimal(amount),
            currency="RON",
            sim_quantity=sim_quantity,
        )
    )
    session.commit()


def _open_month_with_calendar(
    session, faker_tenant, store_id: str, business_date: date = date(2026, 8, 1)
):
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
                business_date,
                store_id,
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    session.commit()
    return month


def _payload_for(rows, person_id: str) -> dict[str, object]:
    for row in rows:
        if row.person_id == person_id:
            return json.loads(row.payload)
    raise AssertionError(f"row not found for {person_id} among {[r.person_id for r in rows]}")


def test_connector_sales_days_divides_monthly_target(session, faker_tenant):
    """Luna finding 1: divisor is the connector sales-day count.

    Monthly target 250000 over 25 selling days => 10000/day. The old code
    divided by the 31 calendar days of August and produced 8064.52.
    """

    _seed_sale(session, faker_tenant, faker_tenant["store_id"], date(2026, 8, 1), "10000")
    _seed_store_target(
        session, faker_tenant, faker_tenant["store_id"], "250000", sales_days=25
    )
    SalaryRepository(session).upsert_window(
        tenant_id=faker_tenant["tenant_id"],
        person_id=faker_tenant["person_a_id"],
        effective_from=date(2026, 1, 1),
        effective_to=None,
        salary=Decimal("2600"),
        tickets=Decimal("480"),
        flip=Decimal("0"),
    )
    session.commit()
    month = _open_month_with_calendar(
        session, faker_tenant, faker_tenant["store_id"]
    )
    _, rows = GridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    payload = _payload_for(rows, faker_tenant["person_a_id"])
    calendar = payload["inputs"]["calendar"]  # type: ignore[index]
    assert calendar, "expected at least one worked day"
    day = calendar[0]
    assert day["target_amount"] == "10000.00"
    target_sources = payload["inputs"]["target_sources"]  # type: ignore[index]
    assert target_sources[0]["sales_days"] == 25
    assert target_sources[0]["divisor"] == 25
    # realised == target => progress 1.00 => commission 3% + 200 bonus.
    components = payload["components"]  # type: ignore[index]
    assert components["main_commission"] == "300"
    assert components["main_bonus"] == "200"
    assert components["target_main"] == "10000.00"


def test_sim_incentive_and_epay_inputs_flow_into_snapshot(session, faker_tenant):
    """Luna finding 2: SIM/incentive/E-pay reach the persisted payload.

    SIM 9 x 3 = 27; incentive 350; E-pay under_50=1 x 5 and at_or_over=10
    x 12 = 125. The old code hardcoded sim_quantity=0, incentive=0 and an
    empty E-pay snapshot.
    """

    _seed_sale(
        session,
        faker_tenant,
        faker_tenant["store_id"],
        date(2026, 8, 1),
        "0",
        sim_quantity=9,
    )
    _seed_store_target(
        session, faker_tenant, faker_tenant["store_id"], "0", sales_days=26
    )
    SalaryRepository(session).upsert_window(
        tenant_id=faker_tenant["tenant_id"],
        person_id=faker_tenant["person_a_id"],
        effective_from=date(2026, 1, 1),
        effective_to=None,
        salary=Decimal("2600"),
        tickets=Decimal("480"),
        flip=Decimal("0"),
    )
    session.add(
        IncentiveInput(
            tenant_id=faker_tenant["tenant_id"],
            person_id=faker_tenant["person_a_id"],
            year=2026,
            month=8,
            version=1,
            amount=Decimal("350"),
            currency="RON",
        )
    )
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    observed_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    session.add_all(
        [
            EpayObservation(
                tenant_id=faker_tenant["tenant_id"],
                store_id=faker_tenant["store_id"],
                person_id=faker_tenant["person_a_id"],
                category=EpayCategory.UNDER_50.value,
                value=1,
                raw_value="1",
                is_valid=True,
                source=month_source(month.id),
                observed_at=observed_at,
            ),
            EpayObservation(
                tenant_id=faker_tenant["tenant_id"],
                store_id=faker_tenant["store_id"],
                person_id=faker_tenant["person_a_id"],
                category=EpayCategory.AT_OR_OVER_50.value,
                value=10,
                raw_value="10",
                is_valid=True,
                source=month_source(month.id),
                observed_at=observed_at,
            ),
        ]
    )
    session.commit()
    month = _open_month_with_calendar(
        session, faker_tenant, faker_tenant["store_id"]
    )
    _, rows = GridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    payload = _payload_for(rows, faker_tenant["person_a_id"])
    calendar = payload["inputs"]["calendar"]  # type: ignore[index]
    assert calendar[0]["sim_quantity"] == 9
    assert payload["inputs"]["parameters"]["incentive"] in {"350", "350.00"}  # type: ignore[index]
    assert payload["inputs"]["epay"] == {"under_50": 1, "at_or_over_50": 10}  # type: ignore[index]
    components = payload["components"]  # type: ignore[index]
    assert components["sim_commission"] == "27"
    assert components["incentive"] == "350"
    assert components["epay_commission"] == "125"
    # 2600 + 480 + 27 (SIM) + 350 (incentive) + 125 (E-pay) = 3582.
    assert components["total_salary"] == "3582"


def test_invalid_epay_observations_do_not_count(session, faker_tenant):
    """Only valid E-pay observations count (rule pack §5)."""

    _seed_sale(
        session, faker_tenant, faker_tenant["store_id"], date(2026, 8, 1), "0"
    )
    _seed_store_target(
        session, faker_tenant, faker_tenant["store_id"], "0", sales_days=26
    )
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    observed_at = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    session.add(
        EpayObservation(
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            person_id=faker_tenant["person_a_id"],
            category=EpayCategory.UNDER_50.value,
            value=None,
            raw_value="abc",
            is_valid=False,
            source=month_source(month.id),
            observed_at=observed_at,
        )
    )
    session.commit()
    snapshot = latest_snapshot(
        session,
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["store_id"],
        person_id=faker_tenant["person_a_id"],
    )
    assert snapshot.under_50_quantity == 0


def test_missing_salary_master_is_an_explicit_anomaly(session, faker_tenant):
    """Luna finding 3: missing HR/payroll master is a deterministic marker."""

    _seed_sale(
        session, faker_tenant, faker_tenant["store_id"], date(2026, 8, 1), "0"
    )
    _seed_store_target(
        session, faker_tenant, faker_tenant["store_id"], "0", sales_days=26
    )
    month = _open_month_with_calendar(
        session, faker_tenant, faker_tenant["store_id"]
    )
    _, rows = GridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    payload = _payload_for(rows, faker_tenant["person_a_id"])
    anomalies = payload["anomalies"]  # type: ignore[index]
    codes = {a["code"] for a in anomalies}
    assert GridAnomalyCode.SALARY_MASTER_MISSING.value in codes
    marker = next(
        a
        for a in anomalies
        if a["code"] == GridAnomalyCode.SALARY_MASTER_MISSING.value
    )
    assert marker["person_id"] == faker_tenant["person_a_id"]
    # The zero substitution is explicit and deterministic, and the payload
    # still carries it.
    assert payload["inputs"]["parameters"]["salary"] == "0"  # type: ignore[index]
    assert payload["inputs"]["parameters"]["tickets"] == "0"  # type: ignore[index]


def test_missing_sales_day_count_is_an_explicit_anomaly(session, faker_tenant):
    """A target without connector sales_days falls back deterministically
    and raises SALES_DAY_COUNT_MISSING instead of silently assuming a
    divisor."""

    _seed_sale(
        session, faker_tenant, faker_tenant["store_id"], date(2026, 8, 1), "0"
    )
    _seed_store_target(
        session, faker_tenant, faker_tenant["store_id"], "0", sales_days=None
    )
    month = _open_month_with_calendar(
        session, faker_tenant, faker_tenant["store_id"]
    )
    _, rows = GridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    payload = _payload_for(rows, faker_tenant["person_a_id"])
    codes = {a["code"] for a in payload["anomalies"]}  # type: ignore[index]
    assert GridAnomalyCode.SALES_DAY_COUNT_MISSING.value in codes


def test_persisted_payload_round_trips_to_inputs_hash(session, faker_tenant):
    """Luna finding 5: the stored payload is the real canonical input dict.

    ``hash_inputs(payload["inputs"])`` must equal the persisted
    ``inputs_hash``, and the calendar section must contain the actual worked
    days. The old code stored a synthetic one-day zero payload whose hash
    disagreed with ``inputs_hash``.
    """

    _seed_sale(
        session,
        faker_tenant,
        faker_tenant["store_id"],
        date(2026, 8, 1),
        "12500",
        sim_quantity=9,
    )
    _seed_store_target(
        session, faker_tenant, faker_tenant["store_id"], "250000", sales_days=25
    )
    SalaryRepository(session).upsert_window(
        tenant_id=faker_tenant["tenant_id"],
        person_id=faker_tenant["person_a_id"],
        effective_from=date(2026, 1, 1),
        effective_to=None,
        salary=Decimal("2600"),
        tickets=Decimal("480"),
        flip=Decimal("0"),
    )
    session.commit()
    month = _open_month_with_calendar(
        session, faker_tenant, faker_tenant["store_id"]
    )
    _, rows = GridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    assert rows, "expected persisted grid rows"
    for row in rows:
        payload = json.loads(row.payload)
        inputs = payload["inputs"]
        assert hash_inputs(inputs) == row.inputs_hash
        assert row.inputs_hash != row.outputs_hash
        # The payload must describe the real month, not a synthetic day.
        assert inputs["rule_pack_version"] == RULE_PACK_VERSION
        assert inputs["revision"] == month.revision
    alice = _payload_for(rows, faker_tenant["person_a_id"])
    calendar = alice["inputs"]["calendar"]  # type: ignore[index]
    assert calendar[0]["business_date"] == "2026-08-01"
    assert calendar[0]["working_kind"] == "NORMAL"
    assert calendar[0]["sim_quantity"] == 9


def test_holiday_marker_is_informational_only(session, faker_tenant):
    """Luna finding 4: holidays ride in canonical inputs but never change
    schedule, Pontaj, target or pay."""

    _seed_sale(
        session, faker_tenant, faker_tenant["store_id"], date(2026, 8, 1), "10000"
    )
    _seed_store_target(
        session, faker_tenant, faker_tenant["store_id"], "100000", sales_days=20
    )
    SalaryRepository(session).upsert_window(
        tenant_id=faker_tenant["tenant_id"],
        person_id=faker_tenant["person_a_id"],
        effective_from=date(2026, 1, 1),
        effective_to=None,
        salary=Decimal("2600"),
        tickets=Decimal("480"),
        flip=Decimal("0"),
    )
    session.commit()
    month = _open_month_with_calendar(
        session, faker_tenant, faker_tenant["store_id"]
    )

    # Baseline: no holiday calendar rows.
    baseline_snapshots, _ = GridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    session.query(GridCalculation).filter_by(month_id=month.id).delete()
    session.commit()
    baseline = next(
        s for s in baseline_snapshots if s.person_id == faker_tenant["person_a_id"]
    )

    # Add a versioned legal holiday plus an admin override on the worked day.
    repo = HolidayRepository(session)
    repo.upsert_calendar(
        tenant_id=faker_tenant["tenant_id"],
        version="rom-legal-2026",
        business_date=date(2026, 8, 1),
        label="Adormirea Maicii Domnului",
        is_active=True,
    )
    repo.upsert_override(
        tenant_id=faker_tenant["tenant_id"],
        version="rom-legal-2026",
        business_date=date(2026, 8, 1),
        is_active=True,
        reason="confirmat de admin",
        actor_id=faker_tenant["user_id"],
    )
    session.commit()

    holiday_snapshots, rows = GridService(session).compute_and_persist(
        tenant_id=faker_tenant["tenant_id"], month=month
    )
    holiday = next(
        s for s in holiday_snapshots if s.person_id == faker_tenant["person_a_id"]
    )

    # Informational only: pay, target, Pontaj totals are untouched.
    assert holiday.components.total_salary == baseline.components.total_salary
    assert holiday.components.target_main == baseline.components.target_main
    assert holiday.components.realised_main == baseline.components.realised_main
    assert holiday.components.main_commission == baseline.components.main_commission

    # The marker + override land in the canonical inputs (hash changes).
    payload = _payload_for(rows, faker_tenant["person_a_id"])
    holidays = payload["inputs"]["holidays"]  # type: ignore[index]
    assert len(holidays) == 1
    marker = holidays[0]
    assert marker["version"] == "rom-legal-2026"
    assert marker["business_date"] == "2026-08-01"
    assert marker["label"] == "Adormirea Maicii Domnului"
    assert marker["override_active"] is True
    assert marker["override_reason"] == "confirmat de admin"
    assert holiday.inputs_hash != baseline.inputs_hash

    # Pontaj summary is untouched too (informational marker).
    assert payload["inputs"]["pontaj"]["working_days"] == 1  # type: ignore[index]


def test_connector_ingest_persists_sim_sales_days_and_incentives(session):
    """The v1 fixture now carries SIM/sales_days/incentives and the ingest
    stores them, so the grid reads connector-authoritative inputs."""

    from ugrile.connectors.fixtures import default_fixture
    from ugrile.connectors.ingest import FixtureConnector

    fx = default_fixture()
    FixtureConnector(session).apply(fx)
    session.commit()

    sale = (
        session.query(SalesStoreDay)
        .filter_by(business_date=date(2026, 8, 1), sim_quantity=9)
        .one()
    )
    assert sale.sim_quantity == 9

    target = session.query(StoreTarget).filter_by(sales_days=26).first()
    assert target is not None
    assert target.sales_days == 26

    incentive = session.query(IncentiveInput).one()
    assert incentive.amount == Decimal("350")
    assert incentive.month == 8
