"""Read-only repository for the persistent Pontaj projection.

The projection is append-only per calendar revision: every apply inserts a
complete ``(active person x every day of month)`` lattice under the new
revision and never mutates rows of previous revisions. This repository only
reads; materialization happens in :class:`ugrile.services.calendar.CalendarService`
inside the same transaction as the calendar CAS.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import PontajProjection


class PontajRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_revision(
        self, tenant_id: str, month_id: str, revision: int
    ) -> list[PontajProjection]:
        rows = self.session.execute(
            select(PontajProjection)
            .where(
                PontajProjection.tenant_id == tenant_id,
                PontajProjection.month_id == month_id,
                PontajProjection.revision == revision,
            )
            .order_by(PontajProjection.person_id, PontajProjection.business_date)
        ).scalars()
        return list(rows)

    def latest_revision(self, tenant_id: str, month_id: str) -> int | None:
        value = self.session.execute(
            select(func.max(PontajProjection.revision)).where(
                PontajProjection.tenant_id == tenant_id,
                PontajProjection.month_id == month_id,
            )
        ).scalar_one_or_none()
        return value


__all__ = ["PontajRepository"]
