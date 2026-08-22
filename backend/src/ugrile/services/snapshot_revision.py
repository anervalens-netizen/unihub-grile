"""Revision identity for revision-bound async reads.

``Month.revision`` is an administrative/CAS revision and also advances on
close/reopen. Calendar-derived data, however, only advances when the calendar
is rebuilt. Async projection/export jobs therefore persist both identities:

* ``month_revision`` prevents executing across a concurrent month state/CAS
  transition;
* ``data_revision`` pins the calendar/Pontaj/attribution snapshot rendered by
  the job.

The calendar write transaction rebuilds current assignments and appends the
Pontaj projection atomically, so their latest revisions must agree whenever
both are present. A disagreement is treated as corrupted/incomplete state and
fails closed.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.errors import DomainError
from ..repositories.models import PontajProjection, SiteDayAssignment


def calendar_data_revision(
    session: Session,
    *,
    tenant_id: str,
    month_id: str,
    fallback_revision: int,
) -> int:
    """Return the current calendar-derived snapshot revision for a month."""

    assignment_revision = session.execute(
        select(func.max(SiteDayAssignment.revision)).where(
            SiteDayAssignment.tenant_id == tenant_id,
            SiteDayAssignment.month_id == month_id,
        )
    ).scalar_one()
    pontaj_revision = session.execute(
        select(func.max(PontajProjection.revision)).where(
            PontajProjection.tenant_id == tenant_id,
            PontajProjection.month_id == month_id,
        )
    ).scalar_one()

    revisions = {
        int(value)
        for value in (assignment_revision, pontaj_revision)
        if value is not None
    }
    if len(revisions) > 1:
        raise DomainError(
            "calendar-derived snapshot revisions disagree",
            details={
                "code": "SNAPSHOT_REVISION_MISMATCH",
                "month_id": month_id,
                "assignment_revision": assignment_revision,
                "pontaj_revision": pontaj_revision,
            },
        )
    if revisions:
        return next(iter(revisions))
    return int(fallback_revision)


__all__ = ["calendar_data_revision"]
