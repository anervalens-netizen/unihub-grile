"""Grid calculation service.

Materialises :class:`ugrile.repositories.models.GridCalculation` rows for
each person/month/revision. The same canonical input hash always produces
the same output hash; the rule pack version is part of the composite
unique key so a future version bump does not collide with the v1
snapshots.

The service is intentionally thin:

* The pure engine lives in :mod:`ugrile.domain.grid`.
* The coefficient bundle lives in :mod:`ugrile.domain.rule_pack`.
* This module owns the IO boundary and the canonical hashing.

Authoritative inputs
--------------------

* Retail-backed targets/incentives use the exact versions pinned by the
  accepted Retail generation; legacy periods keep their historical latest-row
  semantics;
* Pontaj is read only from the exact current calendar revision;
* current WORKING assignments decide who receives a store/day sale;
* sales and SIM values come directly from immutable ``SalesStoreDay`` rows for
  the requested connector generation, so a new connector generation can be
  recalculated without requiring a calendar edit merely to rebuild attribution;
* E-pay and salary master remain versioned/effective authoritative inputs;
* holidays are informational canonical inputs only.

The persisted payload stores the actual canonical inputs used for each snapshot
and round-trips through the stored input/output hashes.
"""

from __future__ import annotations

import json
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..domain.enums import MonthState, WorkingKind
from ..domain.errors import ConflictError
from ..domain.grid import (
    CalendarGridDay,
    GridAnomalyCode,
    GridComponents,
    GridInputs,
    calculate_grid,
)
from ..domain.rule_pack import (
    RULE_PACK_VERSION,
    EpayObservationSnapshot,
    PontajHoursSnapshot,
    RulePackParameters,
    RulePackV1,
    get_default_rule_pack,
    hash_inputs,
)
from ..repositories.epay import latest_snapshot
from ..repositories.holidays import HolidayRepository
from ..repositories.models import (
    GridCalculation,
    IncentiveInput,
    Month,
    Person,
    PontajProjection,
    SalesStoreDay,
    Store,
    StoreTarget,
)
from ..repositories.retail_generation import accepted_retail_generation
from ..repositories.salary import SalaryRepository
from .attribution import AttributionService


@dataclass(frozen=True, slots=True)
class GridSnapshot:
    person_id: str
    store_id: str
    components: GridComponents
    inputs_hash: str
    outputs_hash: str
    anomalies: tuple[dict[str, object], ...]
    canonical_inputs: dict[str, object]


def _anomaly(
    code: GridAnomalyCode,
    *,
    store_id: str | None = None,
    person_id: str | None = None,
    business_date: date | None = None,
    message: str,
) -> dict[str, object]:
    return {
        "code": code.value,
        "store_id": store_id,
        "person_id": person_id,
        "business_date": business_date.isoformat() if business_date else None,
        "message": message,
    }


def _sort_anomalies(
    anomalies: list[dict[str, object]],
) -> tuple[dict[str, object], ...]:
    return tuple(
        sorted(
            anomalies,
            key=lambda a: (
                str(a["code"]),
                str(a["store_id"] or ""),
                str(a["business_date"] or ""),
                str(a["person_id"] or ""),
            ),
        )
    )


def _serialise_components(components: GridComponents) -> dict[str, object]:
    return {
        "salary": format(components.salary, "f"),
        "tickets": format(components.tickets, "f"),
        "main_commission": format(components.main_commission, "f"),
        "main_bonus": format(components.main_bonus, "f"),
        "extra_fixed_pay": format(components.extra_fixed_pay, "f"),
        "extra_other_commission": format(components.extra_other_commission, "f"),
        "sim_commission": format(components.sim_commission, "f"),
        "epay_commission": format(components.epay_commission, "f"),
        "incentive": format(components.incentive, "f"),
        "flip": format(components.flip, "f"),
        "total_salary": format(components.total_salary, "f"),
        "salary_cash": format(components.salary_cash, "f"),
        "extra_days": components.extra_days,
        "progress": format(components.progress, "f"),
        "realised_main": format(components.realised_main, "f"),
        "target_main": format(components.target_main, "f"),
    }


def _serialise_inputs(
    *,
    person_id: str,
    home_store_id: str,
    parameters: RulePackParameters,
    days: list[CalendarGridDay],
    epay: EpayObservationSnapshot,
    pontaj: PontajHoursSnapshot,
    pack: RulePackV1,
    rule_pack_version: str,
    revision: int,
    sales_generation: str,
    holidays: list[dict[str, object]],
    target_sources: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "rule_pack_version": rule_pack_version,
        "rule_pack_hash": pack.canonical_hash(),
        "revision": revision,
        "person_id": person_id,
        "home_store_id": home_store_id,
        "parameters": {
            "salary": format(parameters.salary, "f"),
            "tickets": format(parameters.tickets, "f"),
            "flip": format(parameters.flip, "f"),
            "incentive": format(parameters.incentive, "f"),
        },
        "calendar": [
            {
                "person_id": day.person_id,
                "business_date": day.business_date.isoformat()
                if hasattr(day.business_date, "isoformat")
                else str(day.business_date),
                "working_kind": day.working_kind.value if day.working_kind else None,
                "store_id": day.store_id,
                "target_amount": format(day.target_amount, "f"),
                "sales_amount": format(day.sales_amount, "f"),
                "sim_quantity": day.sim_quantity,
            }
            for day in sorted(days, key=lambda item: (item.business_date, item.store_id or ""))
        ],
        "epay": {
            "under_50": epay.under_50_quantity,
            "at_or_over_50": epay.at_or_over_50_quantity,
        },
        "pontaj": {
            "working_days": pontaj.working_days,
            "working_hours": format(pontaj.working_hours, "f"),
            "leave_days": pontaj.leave_days,
            "off_days": pontaj.off_days,
        },
        "sales_generation": sales_generation,
        "target_sources": [dict(target) for target in target_sources],
        "holidays": [dict(holiday) for holiday in holidays],
    }


class GridService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.pack = get_default_rule_pack()

    def _calendar_days(
        self,
        *,
        tenant_id: str,
        month: Month,
        person_id: str,
        home_store_id: str,
        sales_generation: str,
    ) -> tuple[
        list[CalendarGridDay],
        PontajHoursSnapshot,
        list[dict[str, object]],
        list[dict[str, object]],
    ]:
        """Build exact-revision calendar/Pontaj and generation-bound sales inputs."""

        days_in_month = monthrange(month.year, month.month)[1]
        first = date(month.year, month.month, 1)
        last = date(month.year, month.month, days_in_month)
        period = f"{month.year:04d}-{month.month:02d}"
        accepted = accepted_retail_generation(
            self.session,
            tenant_id=tenant_id,
            period=period,
        )
        if accepted is not None and sales_generation != accepted.generation_key:
            raise ConflictError(
                "grid sales generation is not the accepted Retail head",
                details={
                    "code": "RETAIL_GENERATION_NOT_ACCEPTED",
                    "accepted_generation": accepted.generation_key,
                    "received_generation": sales_generation,
                    "period": period,
                },
            )

        pontaj_rows = list(
            self.session.execute(
                select(PontajProjection).where(
                    PontajProjection.tenant_id == tenant_id,
                    PontajProjection.month_id == month.id,
                    PontajProjection.person_id == person_id,
                    PontajProjection.revision == month.revision,
                )
            ).scalars()
        )
        working_days = 0
        leave_days = 0
        off_days = 0
        working_hours = Decimal("0")
        for row in pontaj_rows:
            if row.status == "WORKING":
                working_days += 1
                working_hours += row.hours
            elif row.status == "LEAVE":
                leave_days += 1
            else:
                off_days += 1
        if not pontaj_rows:
            off_days = days_in_month
        pontaj = PontajHoursSnapshot(
            working_days=working_days,
            working_hours=working_hours,
            leave_days=leave_days,
            off_days=off_days,
        )

        sale_rows = list(
            self.session.execute(
                select(SalesStoreDay).where(
                    SalesStoreDay.tenant_id == tenant_id,
                    SalesStoreDay.business_date >= first,
                    SalesStoreDay.business_date <= last,
                    SalesStoreDay.generation == sales_generation,
                )
            ).scalars()
        )
        sales_index = {
            (row.store_id, row.business_date): row.amount for row in sale_rows
        }
        sim_index = {
            (row.store_id, row.business_date): row.sim_quantity for row in sale_rows
        }
        sales_pairs = set(sales_index)

        target_index: dict[tuple[str, date], Decimal] = {}
        target_sources: list[dict[str, object]] = []
        stores_missing_sales_days: set[str] = set()
        store_targets = list(
            self.session.execute(
                select(StoreTarget).where(
                    StoreTarget.tenant_id == tenant_id,
                    StoreTarget.year == month.year,
                    StoreTarget.month == month.month,
                    StoreTarget.kind == "MONTHLY_SALES",
                )
            ).scalars()
        )
        retail_target_versions: dict[str, int] | None = None
        latest_targets: dict[str, StoreTarget] = {}
        if accepted is not None:
            retail_target_versions = {
                store_id: version
                for store_id, kind, version in accepted.target_versions
                if kind == "MONTHLY_SALES"
            }
            for target_row in store_targets:
                expected_version = retail_target_versions.get(target_row.store_id)
                if expected_version == target_row.version:
                    latest_targets[target_row.store_id] = target_row
        else:
            for target_row in store_targets:
                existing = latest_targets.get(target_row.store_id)
                if existing is None or (target_row.version, target_row.id) > (
                    existing.version,
                    existing.id,
                ):
                    latest_targets[target_row.store_id] = target_row

        for target_row in sorted(latest_targets.values(), key=lambda row: row.store_id):
            if target_row.sales_days is not None:
                divisor = target_row.sales_days
            else:
                divisor = days_in_month
                stores_missing_sales_days.add(target_row.store_id)
            per_day = (target_row.amount / Decimal(divisor)).quantize(Decimal("0.01"))
            for offset in range(days_in_month):
                target_index[
                    (target_row.store_id, date(month.year, month.month, 1 + offset))
                ] = per_day
            target_sources.append(
                {
                    "store_id": target_row.store_id,
                    "version": target_row.version,
                    "monthly_amount": format(target_row.amount, "f"),
                    "sales_days": target_row.sales_days,
                    "divisor": divisor,
                }
            )

        anomalies: list[dict[str, object]] = []
        for store_id in sorted(stores_missing_sales_days):
            anomalies.append(
                _anomaly(
                    GridAnomalyCode.SALES_DAY_COUNT_MISSING,
                    store_id=store_id,
                    person_id=person_id,
                    message=(
                        "target row has no connector sales_days; divisor "
                        "falls back to the calendar month length"
                    ),
                )
            )

        working_assignments = self._working_assignments_by_day(
            tenant_id=tenant_id,
            month=month,
            person_id=person_id,
        )
        missing_target_stores: set[str] = set()
        days: list[CalendarGridDay] = []
        for offset in range(days_in_month):
            business_date = date(month.year, month.month, 1 + offset)
            assignment = working_assignments.get(business_date)
            if assignment is None:
                continue
            store_id = assignment["store_id"]
            sales_amount = sales_index.get((store_id, business_date), Decimal("0"))
            target_amount = target_index.get((store_id, business_date), Decimal("0"))
            if (store_id, business_date) not in sales_pairs:
                anomalies.append(
                    _anomaly(
                        GridAnomalyCode.MISSING_SALE,
                        store_id=store_id,
                        person_id=person_id,
                        business_date=business_date,
                        message=(
                            "worked store/date has no SalesStoreDay row in accepted "
                            f"generation {sales_generation}"
                        ),
                    )
                )
            target_missing = retail_target_versions is not None and (
                store_id not in retail_target_versions or store_id not in latest_targets
            )
            if target_missing:
                if store_id not in missing_target_stores:
                    missing_target_stores.add(store_id)
                    anomalies.append(
                        _anomaly(
                            GridAnomalyCode.TARGET_INPUT_MISSING,
                            store_id=store_id,
                            person_id=person_id,
                            message=(
                                "accepted Retail snapshot has no usable MONTHLY_SALES "
                                "target for a worked store"
                            ),
                        )
                    )
            elif target_amount == 0:
                anomalies.append(
                    _anomaly(
                        GridAnomalyCode.TARGET_ZERO,
                        store_id=store_id,
                        person_id=person_id,
                        business_date=business_date,
                        message="worked day target is zero",
                    )
                )
            days.append(
                CalendarGridDay(
                    person_id=person_id,
                    person_home_store_id=home_store_id,
                    business_date=business_date,
                    working_kind=WorkingKind(assignment["working_kind"])
                    if assignment["working_kind"]
                    else WorkingKind.NORMAL,
                    store_id=store_id,
                    target_amount=target_amount,
                    sales_amount=sales_amount,
                    sim_quantity=sim_index.get((store_id, business_date), 0),
                )
            )
        return days, pontaj, anomalies, target_sources

    def _working_assignments_by_day(
        self, *, tenant_id: str, month: Month, person_id: str
    ) -> dict[date, dict[str, str]]:
        from ..repositories.models import SiteDayAssignment

        stmt = (
            select(SiteDayAssignment)
            .where(
                SiteDayAssignment.tenant_id == tenant_id,
                SiteDayAssignment.month_id == month.id,
                SiteDayAssignment.person_id == person_id,
                SiteDayAssignment.status == "WORKING",
            )
            .order_by(SiteDayAssignment.business_date)
        )
        return {
            row.business_date: {
                "store_id": row.store_id,
                "working_kind": row.working_kind or "NORMAL",
            }
            for row in self.session.execute(stmt).scalars()
        }

    def _incentive_for_with_status(
        self, *, tenant_id: str, person_id: str, month: Month
    ) -> tuple[Decimal, bool]:
        accepted = accepted_retail_generation(
            self.session,
            tenant_id=tenant_id,
            period=f"{month.year:04d}-{month.month:02d}",
        )
        if accepted is not None:
            expected_version = accepted.incentive_version_for(person_id=person_id)
            if expected_version is None:
                return Decimal("0"), True
            stmt = select(IncentiveInput).where(
                IncentiveInput.tenant_id == tenant_id,
                IncentiveInput.person_id == person_id,
                IncentiveInput.year == month.year,
                IncentiveInput.month == month.month,
                IncentiveInput.version == expected_version,
            )
            row = self.session.execute(stmt).scalar_one_or_none()
            return (row.amount, False) if row is not None else (Decimal("0"), True)

        stmt = (
            select(IncentiveInput)
            .where(
                IncentiveInput.tenant_id == tenant_id,
                IncentiveInput.person_id == person_id,
                IncentiveInput.year == month.year,
                IncentiveInput.month == month.month,
            )
            .order_by(IncentiveInput.version.desc(), IncentiveInput.id.desc())
            .limit(1)
        )
        row = self.session.execute(stmt).scalar_one_or_none()
        return (row.amount, False) if row is not None else (Decimal("0"), False)

    def _incentive_for(
        self, *, tenant_id: str, person_id: str, month: Month
    ) -> Decimal:
        return self._incentive_for_with_status(
            tenant_id=tenant_id,
            person_id=person_id,
            month=month,
        )[0]

    def compute_for_person(
        self,
        *,
        tenant_id: str,
        month: Month,
        person_id: str,
        sales_generation: str,
    ) -> GridSnapshot:
        person = self.session.get(Person, person_id)
        if person is None or person.tenant_id != tenant_id:
            raise ValueError(f"person not found: {person_id}")
        anomalies: list[dict[str, object]] = []
        first_day = date(month.year, month.month, 1)
        salary, tickets, flip = SalaryRepository(self.session).find_effective_window(
            tenant_id=tenant_id,
            person_id=person_id,
            on_date=first_day,
        )
        if salary is None or tickets is None or flip is None:
            salary = Decimal("0")
            tickets = Decimal("0")
            flip = Decimal("0")
            anomalies.append(
                _anomaly(
                    GridAnomalyCode.SALARY_MASTER_MISSING,
                    person_id=person_id,
                    message=(
                        "no effective-dated HR/payroll salary master row "
                        "for the month; salary/tickets/flip default to zero"
                    ),
                )
            )
        incentive, incentive_missing = self._incentive_for_with_status(
            tenant_id=tenant_id,
            person_id=person_id,
            month=month,
        )
        if incentive_missing:
            anomalies.append(
                _anomaly(
                    GridAnomalyCode.INCENTIVE_INPUT_MISSING,
                    person_id=person_id,
                    message=(
                        "accepted Retail snapshot has no usable incentive input "
                        "for this person; zero is calculation-only and close-blocking"
                    ),
                )
            )
        parameters = RulePackParameters(
            salary=salary,
            tickets=tickets,
            flip=flip,
            incentive=incentive,
        )
        days, pontaj, day_anomalies, target_sources = self._calendar_days(
            tenant_id=tenant_id,
            month=month,
            person_id=person_id,
            home_store_id=person.home_store_id,
            sales_generation=sales_generation,
        )
        anomalies.extend(day_anomalies)
        epay = latest_snapshot(
            self.session,
            tenant_id=tenant_id,
            month_id=month.id,
            store_id=person.home_store_id,
            person_id=person_id,
        )
        holidays = [
            {
                "version": marker.version,
                "business_date": marker.business_date.isoformat(),
                "label": marker.label,
                "is_active": marker.is_active,
                "override_active": marker.override_active,
                "override_reason": marker.override_reason,
            }
            for marker in HolidayRepository(self.session).markers_for_month(
                tenant_id=tenant_id,
                year=month.year,
                month=month.month,
            )
        ]
        inputs = GridInputs(
            person_id=person_id,
            person_home_store_id=person.home_store_id,
            parameters=parameters,
            calendar_days=tuple(days),
            epay=epay,
            pontaj=pontaj,
        )
        components = calculate_grid(self.pack, inputs)
        canonical_inputs = _serialise_inputs(
            person_id=person_id,
            home_store_id=person.home_store_id,
            parameters=parameters,
            days=days,
            epay=epay,
            pontaj=pontaj,
            pack=self.pack,
            rule_pack_version=RULE_PACK_VERSION,
            revision=month.revision,
            sales_generation=sales_generation,
            holidays=holidays,
            target_sources=target_sources,
        )
        ordered_anomalies = _sort_anomalies(anomalies)
        return GridSnapshot(
            person_id=person_id,
            store_id=person.home_store_id,
            components=components,
            inputs_hash=hash_inputs(canonical_inputs),
            outputs_hash=hash_inputs(_serialise_components(components)),
            anomalies=ordered_anomalies,
            canonical_inputs=canonical_inputs,
        )

    def compute_and_persist(
        self,
        *,
        tenant_id: str,
        month: Month,
        sales_generation: str = "FIXTURE_V1",
    ) -> tuple[list[GridSnapshot], list[GridCalculation]]:
        """Build and atomically replace the exact current-revision snapshots."""

        locked_month = self.session.execute(
            select(Month).where(Month.id == month.id).with_for_update()
        ).scalar_one()
        if locked_month.tenant_id != tenant_id:
            raise ValueError("month belongs to another tenant")
        if locked_month.state == MonthState.CLOSED.value:
            raise ConflictError(
                "month is closed",
                details={"code": "MONTH_CLOSED", "month_id": locked_month.id},
            )
        month = locked_month
        people = list(
            self.session.execute(
                select(Person).where(
                    Person.tenant_id == tenant_id,
                    Person.is_active.is_(True),
                )
            ).scalars()
        )
        snapshots = [
            self.compute_for_person(
                tenant_id=tenant_id,
                month=month,
                person_id=person.id,
                sales_generation=sales_generation,
            )
            for person in people
        ]
        self.session.execute(
            delete(GridCalculation).where(
                GridCalculation.tenant_id == tenant_id,
                GridCalculation.month_id == month.id,
                GridCalculation.revision == month.revision,
                GridCalculation.rule_pack_version == RULE_PACK_VERSION,
            )
        )
        for snapshot in snapshots:
            payload = json.dumps(
                {
                    "inputs": snapshot.canonical_inputs,
                    "components": _serialise_components(snapshot.components),
                    "anomalies": [dict(anomaly) for anomaly in snapshot.anomalies],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            self.session.add(
                GridCalculation(
                    tenant_id=tenant_id,
                    month_id=month.id,
                    store_id=snapshot.store_id,
                    person_id=snapshot.person_id,
                    rule_pack_version=RULE_PACK_VERSION,
                    revision=month.revision,
                    inputs_hash=snapshot.inputs_hash,
                    outputs_hash=snapshot.outputs_hash,
                    payload=payload,
                )
            )
        self.session.flush()
        rows = list(
            self.session.execute(
                select(GridCalculation).where(
                    GridCalculation.tenant_id == tenant_id,
                    GridCalculation.month_id == month.id,
                    GridCalculation.revision == month.revision,
                    GridCalculation.rule_pack_version == RULE_PACK_VERSION,
                )
            ).scalars()
        )
        return snapshots, rows

    def latest_for_person(
        self, *, tenant_id: str, month_id: str, person_id: str
    ) -> GridCalculation | None:
        stmt = (
            select(GridCalculation)
            .where(
                GridCalculation.tenant_id == tenant_id,
                GridCalculation.month_id == month_id,
                GridCalculation.person_id == person_id,
            )
            .order_by(GridCalculation.revision.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()


__all__ = ["AttributionService", "GridService", "GridSnapshot"]

_ = Store
