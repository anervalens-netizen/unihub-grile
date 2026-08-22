"""Reusable realistic dataset for M3 performance contracts.

The fixture intentionally models the operational scale in the program tracker:
80 stores, two people per store, one working person per store/day, daily sales,
monthly targets, current Pontaj rows, E-pay observations, attribution and grid
snapshots. Setup work is never included in measured request latency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy.orm import Session

from ugrile.connectors.google import write_store_projection
from ugrile.domain.enums import DayStatus, MonthState, RoleName, WorkingKind
from ugrile.domain.rule_pack import RULE_PACK_VERSION
from ugrile.repositories.models import (
    EpayObservation,
    GridCalculation,
    Month,
    Person,
    PontajProjection,
    SalesStoreDay,
    SiteDayAssignment,
    Store,
    StoreAssignment,
    StoreTarget,
    Tenant,
    User,
)
from ugrile.services.attribution import AttributionService

PERF_STORE_COUNT = 80
PERF_PEOPLE_PER_STORE = 2
PERF_PERSON_COUNT = PERF_STORE_COUNT * PERF_PEOPLE_PER_STORE
PERF_YEAR = 2026
PERF_MONTH = 8
PERF_DAYS = 31


@dataclass(frozen=True, slots=True)
class PerformanceDataset:
    tenant_id: str
    admin_user_id: str
    month_id: str
    store_ids: tuple[str, ...]
    person_ids: tuple[str, ...]

    @property
    def headers(self) -> dict[str, str]:
        return {
            "X-Ugrile-Identity": self.admin_user_id,
            "X-Ugrile-Tenant": self.tenant_id,
        }


def seed_performance_dataset(session: Session) -> PerformanceDataset:
    """Seed a dense, deterministic 80-store / 160-person August dataset."""

    tenant_id = "tenant_perf"
    admin_user_id = "user_perf_admin"
    month_id = "month_perf_2026-08"

    session.add(
        Tenant(
            id=tenant_id,
            name="Performance Tenant",
            timezone="Europe/Bucharest",
            is_active=True,
        )
    )
    session.add(
        User(
            id=admin_user_id,
            tenant_id=tenant_id,
            email="admin@perf.example",
            display_name="Performance Admin",
            role=RoleName.ADMIN.value,
            is_active=True,
        )
    )
    month = Month(
        id=month_id,
        tenant_id=tenant_id,
        year=PERF_YEAR,
        month=PERF_MONTH,
        state=MonthState.OPEN.value,
        revision=1,
    )
    session.add(month)

    stores: list[Store] = []
    people: list[Person] = []
    store_assignments: list[StoreAssignment] = []
    calendar_rows: list[SiteDayAssignment] = []
    pontaj_rows: list[PontajProjection] = []
    sales_rows: list[SalesStoreDay] = []
    target_rows: list[StoreTarget] = []
    epay_rows: list[EpayObservation] = []
    grid_rows: list[GridCalculation] = []

    for store_index in range(PERF_STORE_COUNT):
        store_id = f"store_perf_{store_index:03d}"
        store = Store(
            id=store_id,
            tenant_id=tenant_id,
            company_code="MOBIUP",
            internal_code=f"P{store_index:03d}",
            external_code=f"ERP-{store_index:03d}",
            name=f"Performance Store {store_index:03d}",
            is_active=True,
        )
        stores.append(store)
        target_rows.append(
            StoreTarget(
                tenant_id=tenant_id,
                store_id=store_id,
                year=PERF_YEAR,
                month=PERF_MONTH,
                kind="MONTHLY_SALES",
                version=1,
                amount=Decimal("100000.00") + Decimal(store_index * 100),
                currency="RON",
                sales_days=PERF_DAYS,
            )
        )

        store_people: list[Person] = []
        for person_offset in range(PERF_PEOPLE_PER_STORE):
            person_index = store_index * PERF_PEOPLE_PER_STORE + person_offset
            person_id = f"person_perf_{person_index:03d}"
            person = Person(
                id=person_id,
                tenant_id=tenant_id,
                internal_code=f"A{person_index:03d}",
                external_code=f"HR-{person_index:03d}",
                display_name=f"Agent Performance {person_index:03d}",
                home_store_id=store_id,
                is_active=True,
            )
            people.append(person)
            store_people.append(person)
            store_assignments.append(
                StoreAssignment(
                    tenant_id=tenant_id,
                    person_id=person_id,
                    store_id=store_id,
                    effective_from=date(PERF_YEAR, PERF_MONTH, 1),
                    effective_to=date(PERF_YEAR, PERF_MONTH, PERF_DAYS),
                )
            )
            for category in ("UNDER_50", "AT_OR_OVER_50"):
                epay_rows.append(
                    EpayObservation(
                        tenant_id=tenant_id,
                        store_id=store_id,
                        person_id=person_id,
                        category=category,
                        value=2,
                        raw_value="2",
                        is_valid=True,
                        source="PERF_FIXTURE",
                        observed_at=datetime(
                            PERF_YEAR,
                            PERF_MONTH,
                            PERF_DAYS,
                            12,
                            0,
                            tzinfo=UTC,
                        ),
                    )
                )
            grid_rows.append(
                GridCalculation(
                    tenant_id=tenant_id,
                    month_id=month_id,
                    store_id=store_id,
                    person_id=person_id,
                    rule_pack_version=RULE_PACK_VERSION,
                    revision=1,
                    inputs_hash=f"inputs-{person_index:03d}".ljust(64, "0"),
                    outputs_hash=f"outputs-{person_index:03d}".ljust(64, "0"),
                    payload="{}",
                )
            )

        for day_number in range(1, PERF_DAYS + 1):
            business_date = date(PERF_YEAR, PERF_MONTH, day_number)
            person = store_people[(day_number - 1) % PERF_PEOPLE_PER_STORE]
            calendar_rows.append(
                SiteDayAssignment(
                    tenant_id=tenant_id,
                    month_id=month_id,
                    store_id=store_id,
                    person_id=person.id,
                    business_date=business_date,
                    status=DayStatus.WORKING.value,
                    working_kind=WorkingKind.NORMAL.value,
                    revision=1,
                    source="PERF_FIXTURE",
                )
            )
            pontaj_rows.append(
                PontajProjection(
                    tenant_id=tenant_id,
                    month_id=month_id,
                    person_id=person.id,
                    business_date=business_date,
                    revision=1,
                    status=DayStatus.WORKING.value,
                    start_time=time(10, 0),
                    end_time=time(21, 0),
                    pause_minutes=60,
                    hours=Decimal("10.00"),
                )
            )
            sales_rows.append(
                SalesStoreDay(
                    tenant_id=tenant_id,
                    store_id=store_id,
                    business_date=business_date,
                    generation="perf-g1",
                    amount=Decimal("1000.00") + Decimal(store_index * 10 + day_number),
                    currency="RON",
                    source_ref=f"perf:{store_index}:{day_number}",
                    sim_quantity=(store_index + day_number) % 5,
                )
            )

    session.add_all(stores)
    session.flush()
    session.add_all(people)
    session.flush()
    session.add_all(store_assignments)
    session.add_all(calendar_rows)
    session.add_all(pontaj_rows)
    session.add_all(sales_rows)
    session.add_all(target_rows)
    session.add_all(epay_rows)
    session.add_all(grid_rows)
    session.flush()

    AttributionService(session).rebuild_for_month(
        month=month,
        tenant_id=tenant_id,
        revision=month.revision,
    )

    # Store screen readback should exercise a real last-good projection rather
    # than only the empty-state path. One representative store is sufficient;
    # provider writes are local fake-adapter DB writes only.
    write_store_projection(
        session,
        tenant_id=tenant_id,
        store_id=stores[0].id,
        generation="perf-g1",
        payload={"grila": {"revision": 1}, "pontaj": {"revision": 1}},
    )
    session.commit()

    return PerformanceDataset(
        tenant_id=tenant_id,
        admin_user_id=admin_user_id,
        month_id=month_id,
        store_ids=tuple(store.id for store in stores),
        person_ids=tuple(person.id for person in people),
    )


__all__ = [
    "PERF_DAYS",
    "PERF_PERSON_COUNT",
    "PERF_STORE_COUNT",
    "PerformanceDataset",
    "seed_performance_dataset",
]
