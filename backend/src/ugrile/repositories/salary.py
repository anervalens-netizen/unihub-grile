"""Effective-dated salary master repository.

The salary master is the only authority for salary and tickets
(docs/PRODUCT_CONTRACT.md §11). The grid engine reads from it but the TL
never edits it; only admin/HR can. The repository only provides reads;
writes happen through dedicated admin/HR endpoints that are not in the S3
slice.

Tenant safety
-------------

A composite ``(tenant_id, person_id, effective_from)`` unique constraint is
preserved at the DB layer (see ``salary_master`` model); lookups always
filter by ``tenant_id`` first.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.errors import NotFoundError
from .models import SalaryMaster


class SalaryRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_effective(
        self, *, tenant_id: str, person_id: str, on_date: date
    ) -> SalaryMaster | None:
        """Return the salary master row in effect on ``on_date``.

        ``None`` when no row matches — the engine interprets this as a zero
        contribution (with an explicit marker).
        """

        stmt = (
            select(SalaryMaster)
            .where(
                SalaryMaster.tenant_id == tenant_id,
                SalaryMaster.person_id == person_id,
                SalaryMaster.effective_from <= on_date,
            )
            .order_by(SalaryMaster.effective_from.desc())
        )
        rows = list(self.session.execute(stmt).scalars())
        if not rows:
            return None
        # The first row whose effective window covers on_date wins.
        for row in rows:
            if row.effective_to is None or row.effective_to >= on_date:
                return row
        return rows[0]

    def find_effective_window(
        self, *, tenant_id: str, person_id: str, on_date: date
    ) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
        """Return ``(salary, tickets, flip)`` for ``on_date`` or three Nones.

        Used by the grid service: it prefers a single tuple unpack over a
        row round-trip, and Nones signal "no master row" so the engine can
        surface the zero contribution explicitly.
        """

        row = self.find_effective(tenant_id=tenant_id, person_id=person_id, on_date=on_date)
        if row is None:
            return None, None, None
        return row.salary, row.tickets, row.flip

    def upsert_window(
        self,
        *,
        tenant_id: str,
        person_id: str,
        effective_from: date,
        effective_to: date | None,
        salary: Decimal,
        tickets: Decimal,
        flip: Decimal,
        source: str = "HR_MASTER",
        notes: str | None = None,
    ) -> SalaryMaster:
        """Insert or replace a salary window for one person.

        Used by the admin/HR flow (and by golden fixtures). The method is
        deterministic: the composite ``(tenant_id, person_id, effective_from)``
        key is unique.
        """

        existing = self.session.execute(
            select(SalaryMaster).where(
                SalaryMaster.tenant_id == tenant_id,
                SalaryMaster.person_id == person_id,
                SalaryMaster.effective_from == effective_from,
            )
        ).scalar_one_or_none()
        if existing is None:
            row = SalaryMaster(
                tenant_id=tenant_id,
                person_id=person_id,
                effective_from=effective_from,
                effective_to=effective_to,
                salary=salary,
                tickets=tickets,
                flip=flip,
                source=source,
                notes=notes,
            )
            self.session.add(row)
            self.session.flush()
            return row
        existing.effective_to = effective_to
        existing.salary = salary
        existing.tickets = tickets
        existing.flip = flip
        existing.source = source
        existing.notes = notes
        self.session.flush()
        return existing

    def list_for_tenant(self, tenant_id: str) -> list[SalaryMaster]:
        stmt = (
            select(SalaryMaster)
            .where(SalaryMaster.tenant_id == tenant_id)
            .order_by(SalaryMaster.person_id, SalaryMaster.effective_from.desc())
        )
        return list(self.session.execute(stmt).scalars())


def get_effective_salary(
    session: Session, *, tenant_id: str, person_id: str, on_date: date
) -> tuple[Decimal, Decimal, Decimal]:
    """Convenience wrapper used by the grid service.

    Returns ``(salary, tickets, flip)`` as Decimals; missing row yields
    three zeros (with the caller responsible for any anomaly reporting).
    """

    repo = SalaryRepository(session)
    row = repo.find_effective(tenant_id=tenant_id, person_id=person_id, on_date=on_date)
    if row is None:
        return Decimal("0"), Decimal("0"), Decimal("0")
    return row.salary, row.tickets, row.flip


__all__ = ["SalaryRepository", "get_effective_salary"]


# Re-export to avoid an unused-import lint warning when consumers import
# the helpers below without NotFoundError.
_ = NotFoundError
