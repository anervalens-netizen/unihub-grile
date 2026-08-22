"""Historical payroll participant and home-store resolution.

Current catalog flags are operational state, not historical payroll truth. A
person who leaves or transfers after working in a month must remain present in
that month's Pontaj/grid/close evidence. Conversely, monthly payroll ownership
must never be guessed when dated store history is ambiguous.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..repositories.models import (
    Month,
    Person,
    PersonDayAbsence,
    SiteDayAssignment,
    StoreAssignment,
)


@dataclass(frozen=True, slots=True)
class PayrollHomeStoreResolution:
    """One safe month-level home-store resolution.

    ``store_id`` is populated only when the month has exactly one defensible
    payroll home store. A person with dated history in multiple stores during
    the same month is deliberately unresolved: the current rule pack has one
    monthly ownership store and the product contract does not define split
    monthly semantics yet.
    """

    store_id: str | None
    candidate_store_ids: tuple[str, ...]
    has_dated_history: bool

    @property
    def ambiguous(self) -> bool:
        return len(self.candidate_store_ids) > 1



def _month_bounds(month: Month) -> tuple[date, date]:
    return (
        date(month.year, month.month, 1),
        date(month.year, month.month, monthrange(month.year, month.month)[1]),
    )



def month_participant_ids(
    session: Session,
    *,
    tenant_id: str,
    month: Month,
) -> set[str]:
    """Return people whose payroll may be relevant to ``month``.

    Rules:
    - any explicit calendar/absence row makes the person a participant;
    - effective-dated ``StoreAssignment`` overlap makes the person a
      participant even if the current catalog row is inactive;
    - current ``is_active`` is a compatibility fallback only for people with no
      ``StoreAssignment`` history at all.

    This prevents a leaver from disappearing from the latest Pontaj/grid simply
    because ``people.is_active`` changed after their worked days.
    """

    first_day, last_day = _month_bounds(month)
    explicit_ids = set(
        session.execute(
            select(SiteDayAssignment.person_id).where(
                SiteDayAssignment.tenant_id == tenant_id,
                SiteDayAssignment.month_id == month.id,
            )
        ).scalars()
    )
    explicit_ids.update(
        session.execute(
            select(PersonDayAbsence.person_id).where(
                PersonDayAbsence.tenant_id == tenant_id,
                PersonDayAbsence.month_id == month.id,
            )
        ).scalars()
    )

    history_ids = set(
        session.execute(
            select(StoreAssignment.person_id).where(StoreAssignment.tenant_id == tenant_id)
        ).scalars()
    )
    overlapping_history_ids = set(
        session.execute(
            select(StoreAssignment.person_id).where(
                StoreAssignment.tenant_id == tenant_id,
                StoreAssignment.effective_from <= last_day,
                or_(
                    StoreAssignment.effective_to.is_(None),
                    StoreAssignment.effective_to >= first_day,
                ),
            )
        ).scalars()
    )
    people = list(
        session.execute(select(Person).where(Person.tenant_id == tenant_id)).scalars()
    )

    participant_ids = set(explicit_ids)
    participant_ids.update(overlapping_history_ids)
    participant_ids.update(
        person.id
        for person in people
        if person.id not in history_ids and person.is_active
    )
    existing_ids = {person.id for person in people}
    return participant_ids & existing_ids



def payroll_home_store_for_month(
    session: Session,
    *,
    tenant_id: str,
    month: Month,
    person: Person,
) -> PayrollHomeStoreResolution:
    """Resolve the only safe month-level payroll home store, if one exists.

    Historical assignments take precedence over the mutable catalog home store.
    If the person has no dated assignment history, the current catalog value is
    the explicit legacy fallback. If dated history exists but the month overlaps
    no assignment, or overlaps more than one distinct store, ``store_id`` is
    ``None`` so payroll calculation/close can fail closed instead of inventing a
    monthly ownership rule.
    """

    first_day, last_day = _month_bounds(month)
    history = list(
        session.execute(
            select(StoreAssignment).where(
                StoreAssignment.tenant_id == tenant_id,
                StoreAssignment.person_id == person.id,
            )
        ).scalars()
    )
    if not history:
        return PayrollHomeStoreResolution(
            store_id=person.home_store_id,
            candidate_store_ids=(person.home_store_id,),
            has_dated_history=False,
        )

    overlapping = [
        assignment
        for assignment in history
        if assignment.effective_from <= last_day
        and (assignment.effective_to is None or assignment.effective_to >= first_day)
    ]
    candidates = tuple(sorted({assignment.store_id for assignment in overlapping}))
    return PayrollHomeStoreResolution(
        store_id=candidates[0] if len(candidates) == 1 else None,
        candidate_store_ids=candidates,
        has_dated_history=True,
    )


__all__ = [
    "PayrollHomeStoreResolution",
    "month_participant_ids",
    "payroll_home_store_for_month",
]
