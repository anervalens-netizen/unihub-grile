"""Add M3 query-driven secondary indexes.

The baseline/performance work exposed a small set of growing-history access
paths whose existing single-column or differently ordered indexes do not match
the production predicates. These indexes are deliberately limited to those
paths; uniqueness and tenant-integrity constraints are unchanged.

Revision ID: f6b8d0e2a4c6
Revises: a4c6e8f0b2d4
Create Date: 2026-08-22 22:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6b8d0e2a4c6"
down_revision: str | None = "a4c6e8f0b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_outbox_pending_due",
        "outbox_jobs",
        ["run_after", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_outbox_running_lease",
        "outbox_jobs",
        ["locked_at", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'RUNNING'"),
    )
    op.create_index(
        "ix_outbox_tenant_status_id",
        "outbox_jobs",
        ["tenant_id", "status", "id"],
        unique=False,
    )
    op.create_index(
        "ix_grid_calculations_current_read",
        "grid_calculations",
        [
            "tenant_id",
            "month_id",
            "rule_pack_version",
            "revision",
            "store_id",
            "person_id",
        ],
        unique=False,
    )
    op.create_index(
        "ix_epay_observations_latest_valid",
        "epay_observations",
        [
            "tenant_id",
            "store_id",
            "person_id",
            "source",
            "observed_at",
            "id",
        ],
        unique=False,
        postgresql_where=sa.text("is_valid IS true"),
    )
    op.create_index(
        "ix_sales_store_day_tenant_date",
        "sales_store_day",
        ["tenant_id", "business_date", "store_id", "generation"],
        unique=False,
    )
    op.create_index(
        "ix_sheet_projection_runs_store_latest",
        "sheet_projection_runs",
        ["tenant_id", "store_id", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sheet_projection_runs_store_latest",
        table_name="sheet_projection_runs",
    )
    op.drop_index("ix_sales_store_day_tenant_date", table_name="sales_store_day")
    op.drop_index(
        "ix_epay_observations_latest_valid",
        table_name="epay_observations",
    )
    op.drop_index(
        "ix_grid_calculations_current_read",
        table_name="grid_calculations",
    )
    op.drop_index("ix_outbox_tenant_status_id", table_name="outbox_jobs")
    op.drop_index("ix_outbox_running_lease", table_name="outbox_jobs")
    op.drop_index("ix_outbox_pending_due", table_name="outbox_jobs")
