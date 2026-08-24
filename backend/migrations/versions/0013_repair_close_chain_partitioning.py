"""Repair legacy close-event digest chains per tenant/month.

Migration 0007 backfilled legacy rows in global timestamp order and carried a
single previous digest across month boundaries. Runtime semantics have always
defined an independent append-only chain per ``(tenant_id, month_id)``. This
run-forward data repair recomputes every existing close event with the correct
partitioned predecessor.

Revision ID: b7c9d1e3f5a7
Revises: 9a1c3e5f7b2d
Create Date: 2026-08-24 12:20:00.000000
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c9d1e3f5a7"
down_revision: str | None = "9a1c3e5f7b2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _event_digest(
    *,
    tenant_id: str,
    month_id: str,
    action: str,
    previous_state: str,
    new_state: str,
    revision_before: int,
    revision_after: int,
    actor_id: str,
    reason: str | None,
    blockers: str,
    previous_event_digest: str | None,
) -> str:
    payload = json.dumps(
        {
            "tenant_id": tenant_id,
            "month_id": month_id,
            "action": action,
            "previous_state": previous_state,
            "new_state": new_state,
            "revision_before": revision_before,
            "revision_after": revision_after,
            "actor_id": actor_id,
            "reason": reason,
            "blockers": blockers,
            "previous_event_digest": previous_event_digest,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _repair_close_chains(conn: sa.engine.Connection) -> None:
    rows = conn.execute(
        sa.text(
            "SELECT id, tenant_id, month_id, action, previous_state, new_state, "
            "revision_before, revision_after, actor_id, reason, blockers "
            "FROM month_close_events "
            "ORDER BY tenant_id, month_id, occurred_at, id"
        )
    ).mappings()
    previous_by_chain: dict[tuple[str, str], str] = {}
    for row in rows:
        chain_key = (row["tenant_id"], row["month_id"])
        previous = previous_by_chain.get(chain_key)
        digest = _event_digest(
            tenant_id=row["tenant_id"],
            month_id=row["month_id"],
            action=row["action"],
            previous_state=row["previous_state"],
            new_state=row["new_state"],
            revision_before=row["revision_before"],
            revision_after=row["revision_after"],
            actor_id=row["actor_id"],
            reason=row["reason"],
            blockers=row["blockers"],
            previous_event_digest=previous,
        )
        conn.execute(
            sa.text(
                "UPDATE month_close_events SET previous_event_digest = :prev, "
                "event_digest = :digest WHERE id = :event_id"
            ),
            {"prev": previous, "digest": digest, "event_id": row["id"]},
        )
        previous_by_chain[chain_key] = digest


def upgrade() -> None:
    _repair_close_chains(op.get_bind())


def downgrade() -> None:
    # Recreating the known-bad global chain would intentionally corrupt audit
    # evidence, so this corrective data migration is intentionally irreversible.
    pass
