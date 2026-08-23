"""Sales attribution projection repository.

Reads immutable ``SalesStoreDay`` rows for a tenant/month, writes
``SalesPersonDayProjection`` rows per calendar revision. Calendar writes and
Retail accepted-generation refreshes both materialize projections inside their
own caller transaction.

When M6 Retail snapshots exist for a period, reads are pinned to the last
accepted Retail generation. Historical generations remain stored for audit but
cannot be summed into current attribution. Legacy/fixture periods without an
accepted Retail head preserve the existing all-generation behavior.
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
from .retail_generation import accepted_retail_generation_key


def store_sales_for_month(
    session: Session,
    *,
    tenant_id: str,
    year: int,
    month: int,
    generation: str | None = None,
    resolve_accepted_generation: bool = True,
) -> list[StoreDaySale]:
    """Return authoritative store/day sales for the tenant/month.

    By default the repository resolves an accepted Retail head itself. A caller
    that already resolved the head can pass ``generation`` and set
    ``resolve_accepted_generation=False``; passing ``None`` in that mode means
    intentional legacy all-generation semantics and avoids a duplicate query.
    """

    first = date(year, month, 1)
    last = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    effective_generation = generation
    if resolve_accepted_generation and effective_generation is None:
        effective_generation = accepted_retail_generation_key(
            session,
            tenant_id=tenant_id,
            period=f"{year:04d}-{month:02d}",
        )
    stmt = select(SalesStoreDay).where(
        SalesStoreDay.tenant_id == tenant_id,
        SalesStoreDay.business_date >= first,
        SalesStoreDay.business_date < last,
    )
    if effective_generation is not None:
        stmt = stmt.where(SalesStoreDay.generation == effective_generation)
    stmt = stmt.order_by(
        SalesStoreDay.store_id,
        SalesStoreDay.business_date,
        SalesStoreDay.generation,
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
    """Run attribution for the current calendar using authoritative sales."""

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
    """Bulk-insert one generation/revision projection snapshot.

    Historical rows stay immutable. The unique constraint includes generation,
    so a Retail head advance can materialize a new projection at the same
    calendar revision without deleting the previous generation's evidence.
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
    generation: str | None = None,
) -> list[SalesPersonDayProjection]:
    stmt = select(SalesPersonDayProjection).where(
        SalesPersonDayProjection.tenant_id == tenant_id,
        SalesPersonDayProjection.month_id == month_id,
        SalesPersonDayProjection.revision == revision,
    )
    if generation is not None:
        stmt = stmt.where(SalesPersonDayProjection.generation == generation)
    stmt = stmt.order_by(
        SalesPersonDayProjection.business_date,
        SalesPersonDayProjection.person_id,
        SalesPersonDayProjection.store_id,
    )
    return list(session.execute(stmt).scalars())


__all__ = [
    "attribute_for_month",
    "list_attribution",
    "persist_attribution",
    "store_sales_for_month",
    "working_days_for_month",
]
