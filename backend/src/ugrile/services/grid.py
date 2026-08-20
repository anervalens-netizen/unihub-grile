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
from ..repositories.models import (
    GridCalculation,
    Month,
    Person,
    PontajProjection,
    SalesPersonDayProjection,
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
    ) -> tuple[list[CalendarGridDay], PontajHoursSnapshot]:
        """Pull calendar + Pontaj rows and project them into the engine shape.

        The Pontaj row count, working days and total hours are aggregated in
        one pass; per-day targets are sourced from ``store_targets`` for
        the worked store/date; sales are sourced from the persisted
        ``sales_person_day_projections`` to keep the grid engine
        deterministic and immutable across calls.
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
        # Pontaj rows exist for every day of the month under the active
        # revision, but in case the schedule is missing we still synthesise
        # them by treating OFF as the default.
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
        # ``PontajProjection`` covers every day; if no rows at all, default
        # to month-length OFF so the totals are well-defined.
        if not assignments:
            off_days = days_in_month

        pontaj = PontajHoursSnapshot(
            working_days=working_days,
            working_hours=working_hours,
            leave_days=leave_days,
            off_days=off_days,
        )

        sales_index: dict[tuple[str, date], Decimal] = {}
        rows = list(
            self.session.execute(
                select(SalesPersonDayProjection).where(
                    SalesPersonDayProjection.tenant_id == tenant_id,
                    SalesPersonDayProjection.month_id == month.id,
                    SalesPersonDayProjection.person_id == person_id,
                )
            ).scalars()
        )
        for sale_row in rows:
            sales_index[(sale_row.store_id, sale_row.business_date)] = sale_row.amount

        target_index: dict[tuple[str, date], Decimal] = {}
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
            # The monthly target is divided evenly across the calendar's
            # business days; the same divisor is used everywhere so the
            # inputs hash stays stable.
            per_day = (target_row.amount / Decimal(days_in_month)).quantize(
                Decimal("0.01")
            )
            for offset in range(days_in_month):
                target_index[(target_row.store_id, date(month.year, month.month, 1 + offset))] = per_day

        # Build one row per (date, store_id) for the person, or a single
        # OFF row per date when the person is not assigned.
        # The grid engine treats absences implicitly (working_kind=None).
        rows_by_day: dict[date, CalendarGridDay | None] = {}
        for offset in range(days_in_month):
            rows_by_day[date(month.year, month.month, 1 + offset)] = None
        assignment_rows = list(
            self.session.execute(
                select(PontajProjection).where(
                    PontajProjection.tenant_id == tenant_id,
                    PontajProjection.month_id == month.id,
                    PontajProjection.person_id == person_id,
                    PontajProjection.status == "WORKING",
                )
            ).scalars()
        )
        # The Pontaj projection does not carry the store_id; pair it with
        # the calendar by (date) using the most recent working assignment
        # on that date for the person. The Pontaj row is filled from the
        # store where the person worked that day.
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
                sim_quantity=0,
            )
        days = [d for d in (rows_by_day[d] for d in sorted(rows_by_day)) if d is not None]
        # Used to anchor the first/last; the engine does not need them
        _ = (first, last)
        # ``assignment_rows`` is consumed for the Pontaj snapshot above;
        # remaining references intentionally kept for future SIM input.
        _ = assignment_rows
        return days, pontaj

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
        # SALARY + TICKETS + FLIP from master; on_date is the first day of
        # the month so the latest effective window is selected.
        first_day = date(month.year, month.month, 1)
        salary, tickets, flip = SalaryRepository(
            self.session
        ).find_effective_window(
            tenant_id=tenant_id, person_id=person_id, on_date=first_day
        )
        # Default to zero when no master row exists.
        if salary is None or tickets is None or flip is None:
            salary = Decimal("0")
            tickets = Decimal("0")
            flip = Decimal("0")
        parameters = RulePackParameters(
            salary=salary,
            tickets=tickets,
            flip=flip,
            incentive=Decimal("0"),
        )

        days, pontaj = self._calendar_days(
            tenant_id=tenant_id,
            month=month,
            person_id=person_id,
            home_store_id=person.home_store_id,
        )
        # E-pay snapshot: only the per-store, per-person row exists in
        # the simple S3 model. The grid engine is given the snapshot as-is;
        # the multi-store expansion lands in S5.
        epay = EpayObservationSnapshot.empty()

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
        )
        inputs_hash = hash_inputs(canonical_inputs)
        outputs_hash = hash_inputs(_serialise_components(components))
        return GridSnapshot(
            person_id=person_id,
            store_id=person.home_store_id,
            components=components,
            inputs_hash=inputs_hash,
            outputs_hash=outputs_hash,
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
        level.
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
                    "inputs": _serialise_inputs(
                        person_id=snapshot.person_id,
                        home_store_id=snapshot.store_id,
                        parameters=RulePackParameters(
                            salary=snapshot.components.salary,
                            tickets=snapshot.components.tickets,
                            flip=snapshot.components.flip,
                            incentive=snapshot.components.incentive,
                        ),
                        days=[
                            CalendarGridDay(
                                person_id=snapshot.person_id,
                                person_home_store_id=snapshot.store_id,
                                business_date=date(2026, 8, 1),
                                working_kind=None,
                                store_id=None,
                                target_amount=Decimal("0"),
                                sales_amount=Decimal("0"),
                                sim_quantity=0,
                            )
                        ],
                        epay=EpayObservationSnapshot.empty(),
                        pontaj=PontajHoursSnapshot(
                            working_days=0,
                            working_hours=Decimal("0"),
                            leave_days=0,
                            off_days=0,
                        ),
                        pack=self.pack,
                        rule_pack_version=RULE_PACK_VERSION,
                        revision=month.revision,
                        sales_generation=sales_generation,
                    ),
                    "components": _serialise_components(snapshot.components),
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

    # Convenience for tests / API: read the latest GridCalculation for one
    # person/month.
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


# Re-export the AttributionService for callers that want both calculations
# from a single service handle.
__all__ = [
    "AttributionService",
    "GridService",
    "GridSnapshot",
]


# ``Store`` is referenced by the schema FKs; the service is given a
# tenant id and uses ``Person`` to discover the home store id. We import
# the model here only to keep mypy happy with the unused import pattern
# used in the rest of the codebase.
_ = Store
