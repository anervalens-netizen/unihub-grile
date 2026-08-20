"""AC-15 remediation — append-only close audit chain digest proof.

Adds the two chain-integrity columns to ``month_close_events``:

* ``previous_event_digest`` — digest of the previous event in the month's
  chain (``NULL`` for the first event).
* ``event_digest`` — deterministic SHA-256 of the event's persisted fields
  chained to ``previous_event_digest``.

The digest scheme (canonical JSON of tenant_id/month_id/action/
previous_state/new_state/revision_before/revision_after/actor_id/reason/
blockers/previous_event_digest, sorted keys, no whitespace) is identical to
``ugrile.domain.close.month_close_event_digest`` and is inlined here so the
migration stays self-contained. Legacy rows are backfilled in chain order
(``occurred_at, id``), then ``event_digest`` is made NOT NULL.

Revision ID: d8e0f2a4b6c8
Revises: c6d8e0f2a4b6
Create Date: 2026-08-21 09:30:00.000000
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8e0f2a4b6c8"
down_revision: str | None = "c6d8e0f2a4b6"
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
    """Mirror of ``ugrile.domain.close.month_close_event_digest``."""
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


def upgrade() -> None:
    op.add_column(
        "month_close_events",
        sa.Column("previous_event_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "month_close_events",
        sa.Column("event_digest", sa.String(length=64), nullable=True),
    )
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, tenant_id, month_id, action, previous_state, new_state, "
            "revision_before, revision_after, actor_id, reason, blockers "
            "FROM month_close_events ORDER BY occurred_at, id"
        )
    ).mappings()
    previous: str | None = None
    for row in rows:
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
        previous = digest
    op.alter_column(
        "month_close_events",
        "event_digest",
        existing_type=sa.String(length=64),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("month_close_events", "event_digest")
    op.drop_column("month_close_events", "previous_event_digest")
