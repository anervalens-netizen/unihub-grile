"""Historical month participant and payroll-home resolution.

Current catalog flags are operational state, not historical payroll truth. A
person who leaves or transfers after working in a month must remain present in
that month's Pontaj/grid/close evidence.
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
from .person_scope import effective_home_store_map


@dataclass(frozen=True, slots=True)
class PayrollHomeStoreResolution:
    store_id: str
    changed_during_month: bool
    unresolved_dates: tuple[date, ...]


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
    - effective-dated StoreAssignment overlap makes the person a participant,
      even if the current catalog row is inactive;
    - current ``is_active`` is a compatibility fallback only for people with no
      StoreAssignment history at all.

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
    """Resolve a deterministic monthly ownership store from historical data.

    The current rule pack persists one monthly home-store id even though
    authorization/classification is effective-dated per day. When dated history
    contains a transfer, the latest resolved home store in the month is used for
    preview/output ownership and ``changed_during_month`` is raised so close can
    fail on the explicit payroll anomaly rather than silently inventing split
    monthly semantics.

    Gaps in known dated history are also reported. For legacy people with no
    dated history, ``effective_home_store_map`` deliberately supplies the
    current catalog home store as its compatibility fallback.
    """

    first_day, last_day = _month_bounds(month)
    dates = {
        date(month.year, month.month, day)
        for day in range(1, monthrange(month.year, month.month)[1] + 1)
    }
    mapping = effective_home_store_map(
        session,
        tenant_id=tenant_id,
        person_ids={person.id},
        business_dates=dates,
    )
    resolved_by_date = {
        business_date: mapping[(person.id, business_date)]
        for business_date in dates
        if (person.id, business_date) in mapping
    }
    unresolved = tuple(sorted(dates - set(resolved_by_date)))
    distinct = {store_id for store_id in resolved_by_date.values()}

    if resolved_by_date:
        latest_date = max(resolved_by_date)
        store_id = resolved_by_date[latest_date]
    else:
        # This can happen only with malformed/gapped dated history. Keep preview
        # deterministic, but the unresolved anomaly must block final close.
        store_id = person.home_store_id

    _ = (first_day, last_day)  # bounds are kept explicit for future policy evolution.
    return PayrollHomeStoreResolution(
        store_id=store_id,
        changed_during_month=len(distinct) > 1,
        unresolved_dates=unresolved,
    )


__all__ = [
    "PayrollHomeStoreResolution",
    "month_participant_ids",
    "payroll_home_store_for_month",
]
