"""Effective-dated manager/TL scope queries."""

from __future__ import annotations

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import ManagerScope


class ManagerScopeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def store_ids_for_user(self, *, tenant_id: str, user_id: str, on_date: date) -> set[str]:
        stmt = select(ManagerScope.store_id).where(
            ManagerScope.tenant_id == tenant_id,
            ManagerScope.user_id == user_id,
            ManagerScope.effective_from <= on_date,
            or_(ManagerScope.effective_to.is_(None), ManagerScope.effective_to >= on_date),
        )
        return set(self.session.execute(stmt).scalars())


__all__ = ["ManagerScopeRepository"]
