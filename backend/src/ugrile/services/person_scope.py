"""Effective-dated person home-store resolution for authorization/read paths.

``Person.home_store_id`` is the current catalog value. Historical program and
Pontaj reads must prefer ``StoreAssignment`` when an effective-dated row exists
for the business date, otherwise a later transfer can leak or hide historical
rows. The resolver batches both catalog and assignment reads so callers do not
fall back to per-row database lookups.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..repositories.models import Person, StoreAssignment


def effective_home_store_map(
    session: Session,
    *,
    tenant_id: str,
    person_ids: set[str],
    business_dates: set[date],
) -> dict[tuple[str, date], str]:
    """Return ``(person, date) -> effective home store`` for requested cells.

    Effective-dated assignments take precedence. When legacy data has no
    assignment history at all, the current ``Person.home_store_id`` is the
    explicit compatibility fallback. Once a person has dated history, a gap in
    that history is unknown state and therefore fails closed: no mapping is
    returned for that person/date. Overlapping assignment rows resolve
    deterministically to the latest ``effective_from`` then highest row id.
    """

    if not person_ids or not business_dates:
        return {}

    people = list(
        session.execute(
            select(Person).where(
                Person.tenant_id == tenant_id,
                Person.id.in_(person_ids),
            )
        ).scalars()
    )
    current_home = {person.id: person.home_store_id for person in people}
    if not current_home:
        return {}

    first_day = min(business_dates)
    last_day = max(business_dates)
    assignments = list(
        session.execute(
            select(StoreAssignment)
            .where(
                StoreAssignment.tenant_id == tenant_id,
                StoreAssignment.person_id.in_(set(current_home)),
            )
            .order_by(
                StoreAssignment.person_id,
                StoreAssignment.effective_from.desc(),
                StoreAssignment.id.desc(),
            )
        ).scalars()
    )
    by_person: dict[str, list[StoreAssignment]] = defaultdict(list)
    for assignment in assignments:
        by_person[assignment.person_id].append(assignment)

    result: dict[tuple[str, date], str] = {}
    for person_id, fallback_store_id in current_home.items():
        history = by_person.get(person_id, [])
        if not history:
            for business_date in business_dates:
                result[(person_id, business_date)] = fallback_store_id
            continue

        # History exists, so every requested date must be explained by an
        # effective row. A transfer gap is intentionally not filled from the
        # mutable current catalog value.
        relevant = [
            assignment
            for assignment in history
            if assignment.effective_from <= last_day
            and (assignment.effective_to is None or assignment.effective_to >= first_day)
        ]
        for business_date in business_dates:
            for assignment in relevant:
                if assignment.effective_from > business_date:
                    continue
                if assignment.effective_to is not None and assignment.effective_to < business_date:
                    continue
                result[(person_id, business_date)] = assignment.store_id
                break
    return result


__all__ = ["effective_home_store_map"]
