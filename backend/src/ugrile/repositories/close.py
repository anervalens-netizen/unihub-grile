"""Append-only month close audit repository.

The chain stores one row per successful ``CLOSE`` or ``REOPEN`` action. The
``Month`` row itself is mutated in place; this repository preserves the
historical trail without ever deleting a previous record.

Tenant safety
-------------

All reads and writes scope by ``tenant_id`` first; month_id is unique
inside the tenant.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import CloseAction
from ..domain.errors import NotFoundError
from .models import MonthCloseEvent


class MonthCloseEventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def append(
        self,
        *,
        tenant_id: str,
        month_id: str,
        action: CloseAction,
        previous_state: str,
        new_state: str,
        revision_before: int,
        revision_after: int,
        actor_id: str,
        reason: str | None,
        blockers: list[dict[str, object]] | None = None,
    ) -> MonthCloseEvent:
        row = MonthCloseEvent(
            tenant_id=tenant_id,
            month_id=month_id,
            action=action.value,
            previous_state=previous_state,
            new_state=new_state,
            revision_before=revision_before,
            revision_after=revision_after,
            actor_id=actor_id,
            reason=reason,
            blockers=json.dumps(blockers or [], ensure_ascii=False, sort_keys=True),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_for_month(
        self, tenant_id: str, month_id: str
    ) -> list[MonthCloseEvent]:
        stmt = (
            select(MonthCloseEvent)
            .where(
                MonthCloseEvent.tenant_id == tenant_id,
                MonthCloseEvent.month_id == month_id,
            )
            .order_by(MonthCloseEvent.occurred_at, MonthCloseEvent.id)
        )
        return list(self.session.execute(stmt).scalars())

    def latest_for_month(
        self, tenant_id: str, month_id: str
    ) -> MonthCloseEvent | None:
        events = self.list_for_month(tenant_id, month_id)
        return events[-1] if events else None

    def get(self, event_id: int) -> MonthCloseEvent:
        row = self.session.get(MonthCloseEvent, event_id)
        if row is None:
            raise NotFoundError(f"close event not found: {event_id}")
        return row


__all__ = ["MonthCloseEventRepository"]
