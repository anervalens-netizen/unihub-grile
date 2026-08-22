"""Transactional append-only audit helpers for business mutations."""

from __future__ import annotations

import json
from collections.abc import Mapping

from sqlalchemy.orm import Session

from ..repositories.models import AuditEvent


def record_audit_event(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str | None,
    action: str,
    entity: str,
    entity_id: str,
    payload: Mapping[str, object],
) -> AuditEvent:
    """Append one audit event inside the caller's current transaction.

    The helper never commits. A rollback of the business mutation therefore
    rolls the audit row back as well; a successful mutation and its evidence
    become visible atomically.
    """

    row = AuditEvent(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        payload=json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    session.add(row)
    session.flush()
    return row


__all__ = ["record_audit_event"]
