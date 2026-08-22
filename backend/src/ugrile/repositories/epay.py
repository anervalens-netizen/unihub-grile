"""Epay observation read helpers.

Only valid Epay observations are exposed to the grid engine. The Epay
ingest writes the rows; this repository aggregates them into the
two-category per-person snapshot the grid engine expects.

Month binding
-------------

The existing ``source`` column carries a deterministic opaque month binding.
This avoids reusing a recent observation from another payroll month while
preserving compatibility with the current schema. Legacy unbound rows are
ignored and therefore fail closed until the month is read back again.

Tenant safety
-------------

All queries scope by ``tenant_id`` first.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import EpayCategory
from ..domain.rule_pack import EpayObservationSnapshot
from .models import EpayObservation


def month_source(month_id: str) -> str:
    """Return the stable source discriminator for one payroll month."""

    digest = hashlib.sha256(month_id.encode("utf-8")).hexdigest()[:20]
    return f"EPAY_M_{digest}"


def latest_snapshot(
    session: Session,
    *,
    tenant_id: str,
    month_id: str,
    store_id: str,
    person_id: str,
) -> EpayObservationSnapshot:
    """Return the latest valid month-bound E-pay snapshot for one person/store.

    Only ``is_valid=True`` rows with the exact requested month discriminator
    count. Invalid or legacy unbound observations stay in the audit trail but
    can never leak into another month's payroll calculation.
    """

    stmt = (
        select(EpayObservation)
        .where(
            EpayObservation.tenant_id == tenant_id,
            EpayObservation.store_id == store_id,
            EpayObservation.person_id == person_id,
            EpayObservation.source == month_source(month_id),
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


__all__ = ["latest_snapshot", "month_source"]
