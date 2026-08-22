"""Locked month-state gates for payroll-affecting writes.

Any write whose result participates in financial close must serialize on the
same ``months`` rows used by close/reopen. A plain state read is insufficient:
a writer can otherwise pass ``OPEN`` while a concurrent close validates and
commits, then persist after the month is already closed.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date

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
    """Lock one in-tenant month and reject writes after financial close."""

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


def _month_bounds(month: Month) -> tuple[date, date]:
    first = date(month.year, month.month, 1)
    last = date(month.year, month.month, monthrange(month.year, month.month)[1])
    return first, last


def _window_overlaps_month(
    month: Month,
    *,
    effective_from: date,
    effective_to: date | None,
) -> bool:
    month_start, month_end = _month_bounds(month)
    return effective_from <= month_end and (
        effective_to is None or effective_to >= month_start
    )


def lock_months_for_salary_write(
    session: Session,
    *,
    tenant_id: str,
    selected_month_id: str,
    effective_from: date,
    effective_to: date | None,
) -> Month:
    """Serialize one effective-dated salary write against every affected close.

    Salary master rows are not intrinsically month-bound. An update submitted
    through an open September screen can still be effective from January and
    therefore alter an already-closed August payroll. To prevent that bypass,
    salary writes lock all existing tenant month rows in deterministic order
    and reject any effective-window overlap with a CLOSED month.

    Locking all tenant months is intentionally conservative; salary-master
    writes are rare and correctness is more important than micro-optimizing a
    small administrative transaction. Deterministic ordering also avoids
    salary-vs-salary deadlocks.
    """

    months = list(
        session.execute(
            select(Month)
            .where(Month.tenant_id == tenant_id)
            .order_by(Month.year, Month.month, Month.id)
            .with_for_update()
        ).scalars()
    )
    selected = next((month for month in months if month.id == selected_month_id), None)
    if selected is None:
        raise NotFoundError("month not found", details={"month_id": selected_month_id})

    closed_overlaps = [
        month.id
        for month in months
        if month.state == MonthState.CLOSED.value
        and _window_overlaps_month(
            month,
            effective_from=effective_from,
            effective_to=effective_to,
        )
    ]
    if closed_overlaps:
        raise ConflictError(
            "salary effective window overlaps a closed month",
            details={
                "code": "MONTH_CLOSED",
                "month_id": selected.id,
                "closed_month_ids": closed_overlaps,
            },
        )
    if selected.state == MonthState.CLOSED.value:
        raise ConflictError(
            "month is closed",
            details={"code": "MONTH_CLOSED", "month_id": selected.id},
        )
    return selected


__all__ = [
    "lock_month_for_financial_write",
    "lock_months_for_salary_write",
]
