"""Transactional append-only audit helpers for business mutations."""

from __future__ import annotations

import json
from collections.abc import Mapping

from sqlalchemy.orm import Session

from ..core.correlation import current_correlation_id
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
    become visible atomically. When invoked inside an API request or worker
    dispatch, the active correlation id is persisted in the audit payload.
    """

    audit_payload = dict(payload)
    correlation_id = current_correlation_id()
    if correlation_id is not None:
        # The active request/job context is authoritative. This intentionally
        # replaces older service-generated audit-only ids so one operation can
        # be traced across API, durable job and audit evidence.
        audit_payload["correlation_id"] = correlation_id

    row = AuditEvent(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        payload=json.dumps(
            audit_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    session.add(row)
    session.flush()
    return row


__all__ = ["record_audit_event"]
