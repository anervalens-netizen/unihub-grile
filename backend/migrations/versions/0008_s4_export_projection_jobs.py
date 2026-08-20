"""S4 — extend ``outbox_jobs.kind`` to allow the export / Google projection kinds.

The S4 slice registers four new job kinds in :mod:`ugrile.worker.jobs` so the
manager UI can schedule export/projection work through the existing
``POST /worker/jobs`` enqueue endpoint, and operators see the rows through
``GET /worker/jobs`` while the worker loop exercises the SKIP LOCKED +
idempotency_key discipline on the seeded fixture.

The handlers persist a typed ``ENQUEUED_S4`` summary into the existing
``import_runs`` / ``export_runs`` tables; the actual XLSX / Google
adapters are owned by S5 and replace this body without touching the
queue.

Revision ID: 1e3b2c4d5f6a
Revises: d8e0f2a4b6c8
Create Date: 2026-08-21 11:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1e3b2c4d5f6a"
down_revision: str | None = "d8e0f2a4b6c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The constraint is replaced atomically: SQLite (used in unit tests) does
    # not support ``ALTER CONSTRAINT``; both dialects handle the same
    # drop+add sequence.
    op.drop_constraint("outbox_kind_enum", "outbox_jobs", type_="check")
    op.create_check_constraint(
        "outbox_kind_enum",
        "outbox_jobs",
        "kind IN ('FIXTURE_INGEST', 'TENANT_BOOTSTRAP', 'NOOP', "
        "'FIXTURE_INGEST_BY_TENANT', 'EXPORT_XLSX_STORE', 'EXPORT_XLSX_BULK', "
        "'EXPORT_PONTAJ_ONLY', 'GOOGLE_PROJECTION_STORE')",
    )


def downgrade() -> None:
    op.drop_constraint("outbox_kind_enum", "outbox_jobs", type_="check")
    op.create_check_constraint(
        "outbox_kind_enum",
        "outbox_jobs",
        "kind IN ('FIXTURE_INGEST', 'TENANT_BOOTSTRAP', 'NOOP', "
        "'FIXTURE_INGEST_BY_TENANT')",
    )
