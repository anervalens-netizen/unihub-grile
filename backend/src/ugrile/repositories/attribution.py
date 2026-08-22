"""Sales attribution projection repository.

Reads immutable ``SalesStoreDay`` rows for a tenant/month, writes
``SalesPersonDayProjection`` rows per calendar revision. All writes happen
inside the calendar CAS transaction so the projection never gets out of sync
with the calendar.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from ..domain.attribution import (
    AttributedSale,
    CalendarWorkingDay,
    StoreDaySale,
    attribute_sales,
)
from ..domain.enums import WorkingKind
from .models import (
    SalesPersonDayProjection,
    SalesStoreDay,
    SiteDayAssignment,
)


def store_sales_for_month(
    session: Session,
    *,
    tenant_id: str,
    year: int,
    month: int,
) -> list[StoreDaySale]:
    """Return every ``SalesStoreDay`` row for the tenant in the month."""

    first = date(year, month, 1)
    last = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    stmt = (
        select(SalesStoreDay)
        .where(
            SalesStoreDay.tenant_id == tenant_id,
            SalesStoreDay.business_date >= first,
            SalesStoreDay.business_date < last,
        )
        .order_by(
            SalesStoreDay.store_id,
            SalesStoreDay.business_date,
            SalesStoreDay.generation,
        )
    )
    rows = list(session.execute(stmt).scalars())
    return [
        StoreDaySale(
            store_id=row.store_id,
            business_date=row.business_date,
            generation=row.generation,
            amount=row.amount,
            currency=row.currency,
        )
        for row in rows
    ]


def working_days_for_month(
    session: Session,
    *,
    tenant_id: str,
    month_id: str,
) -> list[CalendarWorkingDay]:
    """Return every WORKING assignment in the month as a calendar slice."""

    stmt = (
        select(SiteDayAssignment)
        .where(
            SiteDayAssignment.tenant_id == tenant_id,
            SiteDayAssignment.month_id == month_id,
            SiteDayAssignment.status == "WORKING",
        )
        .order_by(
            SiteDayAssignment.store_id,
            SiteDayAssignment.business_date,
            SiteDayAssignment.person_id,
        )
    )
    rows = list(session.execute(stmt).scalars())
    return [
        CalendarWorkingDay(
            person_id=row.person_id,
            store_id=row.store_id,
            business_date=row.business_date,
            working_kind=WorkingKind(row.working_kind)
            if row.working_kind
            else WorkingKind.NORMAL,
        )
        for row in rows
    ]


def attribute_for_month(
    session: Session,
    *,
    tenant_id: str,
    month_id: str,
    year: int,
    month: int,
) -> tuple[list[AttributedSale], tuple[str, ...]]:
    """Run attribution for the current revision of the month.

    Returns the attributed rows and the connector generation(s) that
    produced them, sorted by ``(date, person_id, store_id)`` so callers can
    persist without further sorting.
    """

    sales = store_sales_for_month(session, tenant_id=tenant_id, year=year, month=month)
    working = working_days_for_month(session, tenant_id=tenant_id, month_id=month_id)
    result = attribute_sales(working, sales)
    generations = tuple(sorted({sale.generation for sale in result.attributed if sale.generation}))
    return list(result.attributed), generations


def persist_attribution(
    session: Session,
    *,
    tenant_id: str,
    month_id: str,
    revision: int,
    attributed: list[AttributedSale],
) -> int:
    """Bulk-insert one projection row per attributed sale in the caller's tx.

    Prior revisions are preserved by the unique constraint
    ``uq_sales_person_day_projection``. Bulk execution changes only persistence
    mechanics; the complete revision snapshot and transaction boundary remain
    identical to the row-by-row implementation.
    """

    values = [
        {
            "tenant_id": tenant_id,
            "month_id": month_id,
            "person_id": sale.person_id,
            "store_id": sale.store_id,
            "business_date": sale.business_date,
            "revision": revision,
            "amount": Decimal(sale.amount),
            "currency": sale.currency,
            "generation": sale.generation,
            "working_kind": sale.working_kind.value,
        }
        for sale in attributed
    ]
    if values:
        session.execute(insert(SalesPersonDayProjection), values)
    session.flush()
    return len(values)


def list_attribution(
    session: Session,
    *,
    tenant_id: str,
    month_id: str,
    revision: int,
) -> list[SalesPersonDayProjection]:
    stmt = (
        select(SalesPersonDayProjection)
        .where(
            SalesPersonDayProjection.tenant_id == tenant_id,
            SalesPersonDayProjection.month_id == month_id,
            SalesPersonDayProjection.revision == revision,
        )
        .order_by(
            SalesPersonDayProjection.business_date,
            SalesPersonDayProjection.person_id,
            SalesPersonDayProjection.store_id,
        )
    )
    return list(session.execute(stmt).scalars())


__all__ = [
    "attribute_for_month",
    "list_attribution",
    "persist_attribution",
    "store_sales_for_month",
    "working_days_for_month",
]
