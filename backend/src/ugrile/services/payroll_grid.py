"""Canonical month-level grid orchestration over historical participants.

``GridService`` owns the deterministic calculation primitives. This wrapper
owns who belongs in a payroll month and which historical home store is safe to
use for the one-store-per-month Mobiup payroll model. Current catalog state is
never allowed to rewrite an older month's payroll identity.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..domain.enums import MonthState
from ..domain.errors import ConflictError, ValidationError
from ..domain.grid import GridAnomalyCode, GridInputs, calculate_grid
from ..domain.rule_pack import RULE_PACK_VERSION, RulePackParameters, hash_inputs
from ..repositories.epay import latest_snapshot
from ..repositories.holidays import HolidayRepository
from ..repositories.models import GridCalculation, Month, Person
from ..repositories.salary import SalaryRepository
from .grid import (
    GridService,
    GridSnapshot,
    _anomaly,
    _serialise_components,
    _serialise_inputs,
    _sort_anomalies,
)
from .month_participants import month_participant_ids, payroll_home_store_for_month


class PayrollGridService(GridService):
    """Persist exact-revision grid rows for every historical month participant."""

    def __init__(self, session: Session) -> None:
        super().__init__(session)

    def compute_for_person(
        self,
        *,
        tenant_id: str,
        month: Month,
        person_id: str,
        sales_generation: str,
    ) -> GridSnapshot:
        """Compute one snapshot using the defensible historical payroll home.

        The active rule pack has exactly one monthly home-store dimension. If
        dated history places the person in multiple home stores in the same
        month, calculation fails closed rather than selecting one arbitrarily.
        """

        person = self.session.get(Person, person_id)
        if person is None or person.tenant_id != tenant_id:
            raise ValueError(f"person not found: {person_id}")

        resolution = payroll_home_store_for_month(
            self.session,
            tenant_id=tenant_id,
            month=month,
            person=person,
        )
        if resolution.store_id is None:
            code = (
                "PAYROLL_HOME_STORE_AMBIGUOUS"
                if resolution.ambiguous
                else "PAYROLL_HOME_STORE_UNRESOLVED"
            )
            raise ValidationError(
                "payroll home store cannot be resolved safely for the month",
                details={
                    "code": code,
                    "person_id": person.id,
                    "month_id": month.id,
                    "candidate_store_ids": list(resolution.candidate_store_ids),
                },
            )
        home_store_id = resolution.store_id

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
        incentive = self._incentive_for(
            tenant_id=tenant_id,
            person_id=person_id,
            month=month,
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
            home_store_id=home_store_id,
            sales_generation=sales_generation,
        )
        anomalies.extend(day_anomalies)
        epay = latest_snapshot(
            self.session,
            tenant_id=tenant_id,
            month_id=month.id,
            store_id=home_store_id,
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
            person_home_store_id=home_store_id,
            parameters=parameters,
            calendar_days=tuple(days),
            epay=epay,
            pontaj=pontaj,
        )
        components = calculate_grid(self.pack, inputs)
        canonical_inputs = _serialise_inputs(
            person_id=person_id,
            home_store_id=home_store_id,
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
            store_id=home_store_id,
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
        locked_month = self.session.execute(
            select(Month)
            .where(Month.id == month.id, Month.tenant_id == tenant_id)
            .with_for_update()
        ).scalar_one_or_none()
        if locked_month is None:
            raise ValueError("month belongs to another tenant or does not exist")
        if locked_month.state == MonthState.CLOSED.value:
            raise ConflictError(
                "month is closed",
                details={"code": "MONTH_CLOSED", "month_id": locked_month.id},
            )
        month = locked_month

        participant_ids = month_participant_ids(
            self.session,
            tenant_id=tenant_id,
            month=month,
        )
        people = (
            list(
                self.session.execute(
                    select(Person).where(
                        Person.tenant_id == tenant_id,
                        Person.id.in_(participant_ids),
                    )
                ).scalars()
            )
            if participant_ids
            else []
        )
        snapshots = [
            self.compute_for_person(
                tenant_id=tenant_id,
                month=month,
                person_id=person.id,
                sales_generation=sales_generation,
            )
            for person in sorted(people, key=lambda item: item.id)
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


__all__ = ["PayrollGridService"]
