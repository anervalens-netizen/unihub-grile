"""Epay observation read helpers.

Only valid Epay observations are exposed to the grid engine. The Epay
ingest (S5) writes the rows; this repository aggregates them into the
two-category per-person snapshot the grid engine expects.

Tenant safety
-------------

All queries scope by ``tenant_id`` first.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import EpayCategory
from ..domain.rule_pack import EpayObservationSnapshot
from .models import EpayObservation


def latest_snapshot(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    person_id: str,
) -> EpayObservationSnapshot:
    """Return the latest valid E-pay snapshot for one person at one store.

    Only ``is_valid=True`` rows count; invalid observations stay in the
    audit trail but do not affect the grid.
    """

    stmt = (
        select(EpayObservation)
        .where(
            EpayObservation.tenant_id == tenant_id,
            EpayObservation.store_id == store_id,
            EpayObservation.person_id == person_id,
            EpayObservation.is_valid.is_(True),
        )
        .order_by(EpayObservation.observed_at.desc())
    )
    rows = list(session.execute(stmt).scalars())
    under_50 = 0
    at_or_over_50 = 0
    seen_categories: set[str] = set()
    for row in rows:
        if row.category in seen_categories:
            continue
        if row.value is None:
            continue
        seen_categories.add(row.category)
        if row.category == EpayCategory.UNDER_50.value:
            under_50 = int(row.value)
        elif row.category == EpayCategory.AT_OR_OVER_50.value:
            at_or_over_50 = int(row.value)
    return EpayObservationSnapshot(
        under_50_quantity=under_50,
        at_or_over_50_quantity=at_or_over_50,
    )


__all__ = ["latest_snapshot"]
