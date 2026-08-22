"""Locked month-state gate for payroll-affecting writes.

Any write whose result participates in financial close must serialize on the
same ``months`` row used by close/reopen. A plain state read is insufficient:
a writer can otherwise pass ``OPEN`` while a concurrent close validates and
commits, then persist after the month is already closed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import MonthState
from ..domain.errors import ConflictError, NotFoundError
from ..repositories.models import Month


def lock_month_for_financial_write(
    session: Session,
    *,
    tenant_id: str,
    month_id: str,
) -> Month:
    """Lock one in-tenant month and reject writes after financial close.

    The tenant predicate is part of the locking query, so a cross-tenant caller
    cannot acquire a lock on a foreign month before being denied. The lock is
    deliberately held until the caller's surrounding transaction finishes.
    ``CloseService`` locks the same row, so close and authoritative writes
    cannot cross each other between validation and commit.
    """

    month = session.execute(
        select(Month)
        .where(Month.id == month_id, Month.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if month is None:
        raise NotFoundError("month not found", details={"month_id": month_id})
    if month.state == MonthState.CLOSED.value:
        raise ConflictError(
            "month is closed",
            details={"code": "MONTH_CLOSED", "month_id": month.id},
        )
    return month


__all__ = ["lock_month_for_financial_write"]
