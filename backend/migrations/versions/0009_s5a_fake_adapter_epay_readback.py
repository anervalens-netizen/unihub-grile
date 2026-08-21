"""S5a — fake Google adapter projections + E-pay fresh readback.

AC-12 (fake adapter slice) and AC-13 (bounded E-pay readback). The migration
adds three thin support columns the S5a projection and readback services
need without altering any prior AC contract:

* ``sheet_projection_runs.payload`` — JSON snapshot of the projection that
  the fake adapter wrote for the store; lets the readback path prove that
  the structural shape matches the expected Grila + Pontaj lattice without
  touching the live Google Sheet.
* ``sheet_projection_runs.generation`` — connector generation used by the
  projection. Together with ``last_success_generation`` it is the
  authoritative fingerprint of the last-good projection.
* ``sheet_projection_runs.failures`` — monotonic counter of consecutive
  provider failures since the last successful run; lets the readback path
  detect a degraded adapter without losing the last-good payload.

The ``epay_observations`` table keeps its ``observed_at`` index via a
named index; the readback service scopes queries by the month window and
the ``epay_value_range`` / ``epay_category_enum`` check constraints already
enforce the 0..10 / UNDER_50/AT_OR_OVER_50 contract.

Revision ID: 5a7b9c1d3e2f
Revises: 1e3b2c4d5f6a
Create Date: 2026-08-21 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5a7b9c1d3e2f"
down_revision: str | None = "1e3b2c4d5f6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. sheet_projection_runs: add the S5a structural columns.
    op.add_column(
        "sheet_projection_runs",
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "sheet_projection_runs",
        sa.Column("generation", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "sheet_projection_runs",
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
    )
    # 2. epay_observations: explicit named index for the readback path.
    op.create_index(
        "ix_epay_observations_tenant_month",
        "epay_observations",
        ["tenant_id", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_epay_observations_tenant_month", table_name="epay_observations")
    op.drop_column("sheet_projection_runs", "failures")
    op.drop_column("sheet_projection_runs", "generation")
    op.drop_column("sheet_projection_runs", "payload")
