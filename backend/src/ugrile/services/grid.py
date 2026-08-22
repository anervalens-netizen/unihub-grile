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

Authoritative inputs (AC-09 remediation)
----------------------------------------

The grid reads the frozen contract inputs from their authoritative
sources instead of substituting zeroes (docs/MOBIUP_RULE_PACK.md §1-2, §5):

* per-day targets use the connector ``sales_days`` divisor
  (``target_day = monthly target / zile_vanzare_magazin``); a missing
  ``sales_days`` is a deterministic ``SALES_DAY_COUNT_MISSING`` marker,
  never a silent assumption;* SIM quantity rides on the physical ``SalesStoreDay`` row (latest
  generation per store/date);
* the monthly incentive comes from the versioned ``IncentiveInput`` row
  (latest version per person/month);
* E-pay comes from the latest *valid* month-bound ``EpayObservation`` per category;
* salary/tickets/Flip come from the effective-dated HR/payroll master; a
  missing master row is an explicit ``SALARY_MASTER_MISSING`` marker with
  a documented zero substitution;
* versioned Romanian holiday markers plus admin overrides are serialised
  into the canonical inputs as informational-only data — they never change
  schedule, Pontaj, target or pay.

The persisted payload stores the *actual* canonical inputs used for each
snapshot (not a synthetic row), so ``hash_inputs(payload["inputs"])``
round-trips to the stored ``inputs_hash``.

Tenant safety
-------------

The composite FKs on ``grid_calculations`` keep tenant isolation at the DB
layer; the service always passes ``tenant_id`` explicitly.
"""

from __future__ import annotations

import json
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import WorkingKind
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
    SalesPersonDayProjection,
    SalesStoreDay,
    Store,
    StoreTarget,
)
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
                "person_id": d.person_id,
                "business_date": d.business_date.isoformat()
                if hasattr(d.business_date, "isoformat")
                else str(d.business_date),
                "working_kind": d.working_kind.value
                if d.working_kind
                else None,
                "store_id": d.store_id,
                "target_amount": format(d.target_amount, "f"),
                "sales_amount": format(d.sales_amount, "f"),
                "sim_quantity": d.sim_quantity,
            }
            for d in sorted(
                days, key=lambda d: (d.business_date, d.store_id or "")
            )
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
        "target_sources": [dict(t) for t in target_sources],
        "holidays": [dict(h) for h in holidays],
    }


class GridService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.pack = get_default_rule_pack()

    # --- inputs assembly --------------------------------------------------

    def _calendar_days(
        self,
        *,
        tenant_id: str,
        month: Month,
        person_id: str,
        home_store_id: str,
    ) -> tuple[
        list[CalendarGridDay],
        PontajHoursSnapshot,
        list[dict[str, object]],
        list[dict[str, object]],
    ]:
        """Pull calendar + Pontaj + sales rows and project the engine shape.

        The Pontaj row count, working days and total hours are aggregated in
        one pass; per-day targets use the connector-authoritative
        ``sales_days`` divisor (with an explicit marker when the count is
        missing); sales amounts come from the persisted
        ``sales_person_day_projections`` (deterministic and immutable across
        calls) while SIM quantities ride on the physical
        ``sales_store_day`` rows (latest generation per store/date).

        Returns ``(days, pontaj, anomalies, target_sources)``.
        """

        days_in_month = monthrange(month.year, month.month)[1]
        first = date(month.year, month.month, 1)
        last = date(month.year, month.month, days_in_month)

        assignments = list(
            self.session.execute(
                select(PontajProjection).where(
                    PontajProjection.tenant_id == tenant_id,
                    PontajProjection.month_id == month.id,
                    PontajProjection.person_id == person_id,
                )
            ).scalars()
        )
        working_days = 0
        leave_days = 0
        off_days = 0
        working_hours = Decimal("0")
        for row in assignments:
            if row.status == "WORKING":
                working_days += 1
                working_hours += row.hours
            elif row.status == "LEAVE":
                leave_days += 1
            else:
                off_days += 1
        if not assignments:
            off_days = days_in_month

        pontaj = PontajHoursSnapshot(
            working_days=working_days,
            working_hours=working_hours,
            leave_days=leave_days,
            off_days=off_days,
        )

        sales_index: dict[tuple[str, date], Decimal] = {}
        proj_rows = list(
            self.session.execute(
                select(SalesPersonDayProjection).where(
                    SalesPersonDayProjection.tenant_id == tenant_id,
                    SalesPersonDayProjection.month_id == month.id,
                    SalesPersonDayProjection.person_id == person_id,
                )
            ).scalars()
        )
        for proj_row in proj_rows:
            sales_index[(proj_row.store_id, proj_row.business_date)] = proj_row.amount

        sale_rows = list(
            self.session.execute(
                select(SalesStoreDay).where(
                    SalesStoreDay.tenant_id == tenant_id,
                    SalesStoreDay.business_date >= first,
                    SalesStoreDay.business_date <= last,
                )
            ).scalars()
        )
        sales_pairs: set[tuple[str, date]] = set()
        latest_generation: dict[tuple[str, date], str] = {}
        for sale_row in sale_rows:
            pair = (sale_row.store_id, sale_row.business_date)
            sales_pairs.add(pair)
            if pair not in latest_generation or sale_row.generation > latest_generation[pair]:
                latest_generation[pair] = sale_row.generation
        sim_index: dict[tuple[str, date], int] = {}
        for sale_row in sale_rows:
            pair = (sale_row.store_id, sale_row.business_date)
            if latest_generation.get(pair) == sale_row.generation:
                sim_index[pair] = sale_row.sim_quantity

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
        for target_row in store_targets:
            if target_row.sales_days is not None:
                divisor = target_row.sales_days
            else:
                divisor = days_in_month
                stores_missing_sales_days.add(target_row.store_id)
            per_day = (target_row.amount / Decimal(divisor)).quantize(
                Decimal("0.01")
            )
            for offset in range(days_in_month):
                target_index[(target_row.store_id, date(month.year, month.month, 1 + offset))] = per_day
            target_sources.append(
                {
                    "store_id": target_row.store_id,
                    "monthly_amount": format(target_row.amount, "f"),
                    "sales_days": target_row.sales_days,
                    "divisor": divisor,
                }
            )
        target_sources.sort(key=lambda t: str(t["store_id"]))

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

        rows_by_day: dict[date, CalendarGridDay | None] = {}
        for offset in range(days_in_month):
            rows_by_day[date(month.year, month.month, 1 + offset)] = None
        working_assignments_by_day = self._working_assignments_by_day(
            tenant_id=tenant_id, month=month, person_id=person_id
        )
        for offset in range(days_in_month):
            d = date(month.year, month.month, 1 + offset)
            asg = working_assignments_by_day.get(d)
            if asg is None:
                continue
            store_id = asg["store_id"]
            sales_amount = sales_index.get((store_id, d), Decimal("0"))
            target_amount = target_index.get((store_id, d), Decimal("0"))
            if (store_id, d) not in sales_pairs:
                anomalies.append(
                    _anomaly(
                        GridAnomalyCode.MISSING_SALE,
                        store_id=store_id,
                        person_id=person_id,
                        business_date=d,
                        message="worked store/date has no SalesStoreDay row",
                    )
                )
            if target_amount == 0:
                anomalies.append(
                    _anomaly(
                        GridAnomalyCode.TARGET_ZERO,
                        store_id=store_id,
                        person_id=person_id,
                        business_date=d,
                        message="worked day target is zero",
                    )
                )
            rows_by_day[d] = CalendarGridDay(
                person_id=person_id,
                person_home_store_id=home_store_id,
                business_date=d,
                working_kind=WorkingKind(asg["working_kind"])
                if asg["working_kind"]
                else WorkingKind.NORMAL,
                store_id=store_id,
                target_amount=target_amount,
                sales_amount=sales_amount,
                sim_quantity=sim_index.get((store_id, d), 0),
            )
        days = [d for d in (rows_by_day[d] for d in sorted(rows_by_day)) if d is not None]
        _ = (first, last)
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
            row.business_date: {"store_id": row.store_id, "working_kind": row.working_kind or "NORMAL"}
            for row in self.session.execute(stmt).scalars()
        }

    def _incentive_for(self, *, tenant_id: str, person_id: str, month: Month) -> Decimal:
        """Return the latest versioned incentive for the person/month.

        No row means no campaign — a legitimate zero, not an anomaly.
        """

        stmt = (
            select(IncentiveInput)
            .where(
                IncentiveInput.tenant_id == tenant_id,
                IncentiveInput.person_id == person_id,
                IncentiveInput.year == month.year,
                IncentiveInput.month == month.month,
            )
            .order_by(IncentiveInput.version.desc())
            .limit(1)
        )
        row = self.session.execute(stmt).scalar_one_or_none()
        return row.amount if row is not None else Decimal("0")

    # --- public API -------------------------------------------------------

    def compute_for_person(
        self,
        *,
        tenant_id: str,
        month: Month,
        person_id: str,
        sales_generation: str,
    ) -> GridSnapshot:
        """Build the GridInputs, run the engine, return the snapshot.

        ``sales_generation`` is the connector generation the grid should
        bind to; the same value flows into ``inputs_hash`` so the grid
        evolves deterministically with the connector.
        """

        person = self.session.get(Person, person_id)
        if person is None or person.tenant_id != tenant_id:
            raise ValueError(f"person not found: {person_id}")
        anomalies: list[dict[str, object]] = []
        first_day = date(month.year, month.month, 1)
        salary, tickets, flip = SalaryRepository(
            self.session
        ).find_effective_window(
            tenant_id=tenant_id, person_id=person_id, on_date=first_day
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
        incentive = self._incentive_for(
            tenant_id=tenant_id, person_id=person_id, month=month
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
                tenant_id=tenant_id, year=month.year, month=month.month
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
            days=list(days),
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
        inputs_hash = hash_inputs(canonical_inputs)
        outputs_hash = hash_inputs(_serialise_components(components))
        return GridSnapshot(
            person_id=person_id,
            store_id=person.home_store_id,
            components=components,
            inputs_hash=inputs_hash,
            outputs_hash=outputs_hash,
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
        """Build snapshots for every person in the tenant and persist them.

        Each row carries ``(person, month, rule_pack_version, revision)`` as
        the unique key; re-running the same computation produces the same
        hashes so the unique constraint rejects duplicates on the SQL
        level. The persisted payload stores the *actual* canonical inputs
        used for the snapshot (the same dict that produced ``inputs_hash``),
        the decomposed components and the deterministic anomaly markers.
        """

        people = list(
            self.session.execute(
                select(Person).where(
                    Person.tenant_id == tenant_id, Person.is_active.is_(True)
                )
            ).scalars()
        )
        snapshots: list[GridSnapshot] = []
        for person in people:
            snapshot = self.compute_for_person(
                tenant_id=tenant_id,
                month=month,
                person_id=person.id,
                sales_generation=sales_generation,
            )
            snapshots.append(snapshot)
            payload = json.dumps(
                {
                    "inputs": snapshot.canonical_inputs,
                    "components": _serialise_components(snapshot.components),
                    "anomalies": [dict(a) for a in snapshot.anomalies],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            row = GridCalculation(
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
            self.session.add(row)
        self.session.flush()
        return snapshots, list(
            self.session.execute(
                select(GridCalculation).where(
                    GridCalculation.tenant_id == tenant_id,
                    GridCalculation.month_id == month.id,
                    GridCalculation.revision == month.revision,
                )
            ).scalars()
        )

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


__all__ = [
    "AttributionService",
    "GridService",
    "GridSnapshot",
]

_ = Store
