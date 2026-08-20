"""Month repository."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import MonthState
from ..domain.errors import ConflictError, NotFoundError
from ..domain.identifiers import make_month_id
from .models import Month


class MonthRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, month_id: str) -> Month:
        month = self.session.get(Month, month_id)
        if month is None:
            raise NotFoundError(f"month not found: {month_id}")
        return month

    def find(self, tenant_id: str, year: int, month: int) -> Month | None:
        return self.session.get(Month, make_month_id(tenant_id, year, month))

    def get_or_create(self, tenant_id: str, year: int, month: int) -> Month:
        m = self.find(tenant_id, year, month)
        if m is not None:
            return m
        m = Month(
            id=make_month_id(tenant_id, year, month),
            tenant_id=tenant_id,
            year=year,
            month=month,
            state=MonthState.DRAFT,
            revision=0,
        )
        self.session.add(m)
        self.session.flush()
        return m

    def list_for_tenant(self, tenant_id: str) -> list[Month]:
        stmt = (
            select(Month)
            .where(Month.tenant_id == tenant_id)
            .order_by(Month.year.desc(), Month.month.desc())
        )
        return list(self.session.execute(stmt).scalars())

    def assert_open(self, month_id: str) -> Month:
        month = self.get(month_id)
        if month.state in {MonthState.CLOSED}:
            raise ConflictError(
                f"month is closed: {month_id}",
                details={"month_id": month_id, "state": month.state},
            )
        return month

    def bump_revision(self, month_id: str) -> Month:
        month = self.get(month_id)
        month.revision += 1
        return month


__all__ = ["MonthRepository"]
