"""Scope outbox idempotency by tenant and job kind.

The original global uniqueness of ``idempotency_key`` made unrelated tenants
and job kinds compete for the same human-readable key. Idempotency is a logical
operation boundary, so the durable identity is ``(tenant_id, kind,
idempotency_key)``.

Revision ID: a4c6e8f0b2d4
Revises: 5a7b9c1d3e2f
Create Date: 2026-08-22 14:35:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a4c6e8f0b2d4"
down_revision: str | None = "5a7b9c1d3e2f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_outbox_idempotency", "outbox_jobs", type_="unique")
    op.create_unique_constraint(
        "uq_outbox_tenant_kind_idempotency",
        "outbox_jobs",
        ["tenant_id", "kind", "idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_outbox_tenant_kind_idempotency",
        "outbox_jobs",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_outbox_idempotency",
        "outbox_jobs",
        ["idempotency_key"],
    )
