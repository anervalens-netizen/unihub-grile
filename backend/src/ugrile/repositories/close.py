"""Append-only month close audit repository.

The chain stores one row per successful ``CLOSE`` or ``REOPEN`` action. The
``Month`` row itself is mutated in place; this repository preserves the
historical trail without ever deleting a previous record.

Chain integrity
---------------

Every appended row carries ``previous_event_digest`` (the digest of the
previous event in the month's chain, ``None`` for the first event) and
``event_digest`` — the deterministic SHA-256 of the row's persisted fields
chained to ``previous_event_digest`` (see ``ugrile.domain.close``).
:meth:`verify_chain` recomputes the whole chain and reports broken links.
Because appends happen inside the transaction that holds the ``Month`` row
``FOR UPDATE``, concurrent transitions of the same month are serialized and
the chain cannot interleave.

Tenant safety
-------------

All reads and writes scope by ``tenant_id`` first; month_id is unique
inside the tenant.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.close import (
    MonthCloseEventRecord,
    month_close_event_digest,
    verify_month_close_chain,
)
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
        previous = self.latest_for_month(tenant_id, month_id)
        previous_event_digest = previous.event_digest if previous is not None else None
        blockers_json = json.dumps(blockers or [], ensure_ascii=False, sort_keys=True)
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
            blockers=blockers_json,
            previous_event_digest=previous_event_digest,
            event_digest=month_close_event_digest(
                tenant_id=tenant_id,
                month_id=month_id,
                action=action.value,
                previous_state=previous_state,
                new_state=new_state,
                revision_before=revision_before,
                revision_after=revision_after,
                actor_id=actor_id,
                reason=reason,
                blockers=blockers_json,
                previous_event_digest=previous_event_digest,
            ),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_for_month(self, tenant_id: str, month_id: str) -> list[MonthCloseEvent]:
        stmt = (
            select(MonthCloseEvent)
            .where(
                MonthCloseEvent.tenant_id == tenant_id,
                MonthCloseEvent.month_id == month_id,
            )
            .order_by(MonthCloseEvent.occurred_at, MonthCloseEvent.id)
        )
        return list(self.session.execute(stmt).scalars())

    def latest_for_month(self, tenant_id: str, month_id: str) -> MonthCloseEvent | None:
        events = self.list_for_month(tenant_id, month_id)
        return events[-1] if events else None

    def verify_chain(self, tenant_id: str, month_id: str) -> list[str]:
        """Recompute the chain digest and return integrity issues (empty = ok)."""

        records = [
            MonthCloseEventRecord(
                id=event.id,
                tenant_id=event.tenant_id,
                month_id=event.month_id,
                action=event.action,
                previous_state=event.previous_state,
                new_state=event.new_state,
                revision_before=event.revision_before,
                revision_after=event.revision_after,
                actor_id=event.actor_id,
                reason=event.reason,
                blockers=event.blockers,
                previous_event_digest=event.previous_event_digest,
                event_digest=event.event_digest,
            )
            for event in self.list_for_month(tenant_id, month_id)
        ]
        return verify_month_close_chain(records)

    def get(self, event_id: int) -> MonthCloseEvent:
        row = self.session.get(MonthCloseEvent, event_id)
        if row is None:
            raise NotFoundError(f"close event not found: {event_id}")
        return row


__all__ = ["MonthCloseEventRepository"]
