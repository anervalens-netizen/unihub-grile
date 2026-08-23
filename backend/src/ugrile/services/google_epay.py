"""Month-scoped E-pay readback from the bound Google Sheet.

Network I/O normally happens before the month/binding write locks. A month
already known CLOSED is rejected before provider I/O. Before any local
observation is persisted, the service revalidates the calendar revision,
working-person set and complete Sheet binding identity under locks. Drift is
recorded as an invalid latest attempt, so an older valid snapshot cannot make
financial close look fresh after a stale/ambiguous Sheet read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..connectors.google_epay_layout import epay_read_range, parse_epay_readback
from ..domain.enums import MonthState
from ..domain.errors import NotFoundError
from ..repositories.models import Person, SiteDayAssignment
from ..repositories.months import MonthRepository
from .epay import EpayReadbackResult, record_readback
from .month_write_gate import lock_month_for_financial_write
from .sheet_bindings import get_sheet_binding, require_sheet_binding
from .snapshot_revision import calendar_data_revision


class GoogleValuesReader(Protocol):
    def read_values(self, spreadsheet_id: str, range_a1: str) -> list[list[Any]]:
        """Read one bounded Google values range."""
        ...


@dataclass(frozen=True, slots=True)
class GoogleEpayReadbackResult:
    readback: EpayReadbackResult
    structure_valid: bool
    structural_errors: tuple[str, ...]


def _working_person_ids(
    session: Session,
    *,
    tenant_id: str,
    month_id: str,
    store_id: str,
) -> list[str]:
    person_ids = sorted(
        set(
            session.execute(
                select(SiteDayAssignment.person_id).where(
                    SiteDayAssignment.tenant_id == tenant_id,
                    SiteDayAssignment.month_id == month_id,
                    SiteDayAssignment.store_id == store_id,
                    SiteDayAssignment.status == "WORKING",
                )
            ).scalars()
        )
    )
    if not person_ids:
        return []
    existing = set(
        session.execute(
            select(Person.id).where(
                Person.tenant_id == tenant_id,
                Person.id.in_(person_ids),
            )
        ).scalars()
    )
    return [person_id for person_id in person_ids if person_id in existing]


def _invalid_observations(person_ids: list[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for person_id in person_ids:
        result.extend(
            [
                {"person_id": person_id, "category": "UNDER_50", "value": None},
                {
                    "person_id": person_id,
                    "category": "AT_OR_OVER_50",
                    "value": None,
                },
            ]
        )
    return result


def _empty_readback(*, store_id: str, month_id: str) -> EpayReadbackResult:
    return EpayReadbackResult(
        store_id=store_id,
        month_id=month_id,
        observed_at=datetime.now(tz=UTC),
        items=(),
        valid_count=0,
        invalid_count=0,
    )


def read_epay_from_google_sheet(
    session: Session,
    *,
    tenant_id: str,
    month_id: str,
    store_id: str,
    actor_id: str,
    reader: GoogleValuesReader,
) -> GoogleEpayReadbackResult:
    """Read exact E-pay cells from the current bound Sheet and persist the attempt."""

    initial_month = MonthRepository(session).get(month_id)
    if initial_month.tenant_id != tenant_id:
        raise NotFoundError(
            "month not found",
            details={"tenant_id": tenant_id, "month_id": month_id},
        )
    if initial_month.state == MonthState.CLOSED.value:
        lock_month_for_financial_write(
            session,
            tenant_id=tenant_id,
            month_id=month_id,
        )
    initial_binding = require_sheet_binding(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
    )
    initial_identity = (
        initial_binding.spreadsheet_id,
        initial_binding.sheet_name_grila,
        initial_binding.sheet_name_pontaj,
    )
    initial_revision = calendar_data_revision(
        session,
        tenant_id=tenant_id,
        month_id=month_id,
        fallback_revision=initial_month.revision,
    )
    initial_people = _working_person_ids(
        session,
        tenant_id=tenant_id,
        month_id=month_id,
        store_id=store_id,
    )
    if not initial_people:
        lock_month_for_financial_write(
            session,
            tenant_id=tenant_id,
            month_id=month_id,
        )
        return GoogleEpayReadbackResult(
            readback=_empty_readback(store_id=store_id, month_id=month_id),
            structure_valid=True,
            structural_errors=(),
        )

    rows = reader.read_values(
        initial_identity[0],
        epay_read_range(initial_identity[1]),
    )
    parsed = parse_epay_readback(
        rows,
        month_id=month_id,
        revision=initial_revision,
        expected_person_ids=initial_people,
    )

    locked_month = lock_month_for_financial_write(
        session,
        tenant_id=tenant_id,
        month_id=month_id,
    )
    current_binding = get_sheet_binding(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        lock=True,
    )
    current_identity = (
        (
            current_binding.spreadsheet_id,
            current_binding.sheet_name_grila,
            current_binding.sheet_name_pontaj,
        )
        if current_binding is not None
        else None
    )
    current_revision = calendar_data_revision(
        session,
        tenant_id=tenant_id,
        month_id=month_id,
        fallback_revision=locked_month.revision,
    )
    current_people = _working_person_ids(
        session,
        tenant_id=tenant_id,
        month_id=month_id,
        store_id=store_id,
    )

    errors = list(parsed.structural_errors)
    local_drift = (
        current_identity != initial_identity
        or current_revision != initial_revision
        or current_people != initial_people
    )
    if local_drift:
        errors.append("EPAY_LAYOUT_LOCAL_STATE_CHANGED")
    structure_valid = parsed.structure_valid and not local_drift

    if not current_people:
        return GoogleEpayReadbackResult(
            readback=_empty_readback(store_id=store_id, month_id=month_id),
            structure_valid=structure_valid,
            structural_errors=tuple(dict.fromkeys(errors)),
        )

    observations = (
        list(parsed.observations)
        if structure_valid
        else _invalid_observations(current_people)
    )
    readback = record_readback(
        session,
        tenant_id=tenant_id,
        month_id=month_id,
        store_id=store_id,
        observations=observations,
        actor_id=actor_id,
    )
    return GoogleEpayReadbackResult(
        readback=readback,
        structure_valid=structure_valid,
        structural_errors=tuple(dict.fromkeys(errors)),
    )


__all__ = [
    "GoogleEpayReadbackResult",
    "GoogleValuesReader",
    "read_epay_from_google_sheet",
]
