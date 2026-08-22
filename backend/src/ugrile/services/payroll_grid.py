"""Canonical month-level grid orchestration over historical participants.

``GridService`` owns deterministic per-person calculation. This wrapper owns
who belongs in a payroll month: current ``is_active`` alone is not historical
evidence, so leavers/transfers with month participation must still receive a
snapshot.
"""

from __future__ import annotations

import json

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..domain.enums import MonthState
from ..domain.errors import ConflictError
from ..domain.rule_pack import RULE_PACK_VERSION
from ..repositories.models import GridCalculation, Month, Person
from .grid import GridService, GridSnapshot, _serialise_components
from .month_participants import month_participant_ids


class PayrollGridService(GridService):
    """Persist exact-revision grid rows for every historical month participant."""

    def __init__(self, session: Session) -> None:
        super().__init__(session)

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
