"""S3 AC-09 remediation — connector-authoritative grid inputs.

The packet adds the connector inputs the grid engine was silently
substituting (docs/MOBIUP_RULE_PACK.md §1–2, §5):

* ``sales_store_day.sim_quantity`` — per store-day SIM quantity from the
  sales source (defaults to ``0`` for existing rows).
* ``store_targets.sales_days`` — the connector-authoritative selling-day
  count (``zile_vanzare_magazin``) used to divide the monthly target;
  ``NULL`` means the connector did not provide it and the grid raises an
  explicit ``SALES_DAY_COUNT_MISSING`` marker.
* ``incentive_inputs`` — versioned monthly per-person incentive from
  Campaigns/connector (latest version per person/month is consumed).

All constraint names follow the shared ``NAMING_CONVENTION`` so
``alembic check`` reports zero drift against the ORM models.

Revision ID: c6d8e0f2a4b6
Revises: e7f3a9b1c2d4
Create Date: 2026-08-20 23:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c6d8e0f2a4b6"
down_revision: str | None = "e7f3a9b1c2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sales_store_day",
        sa.Column("sim_quantity", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "store_targets",
        sa.Column("sales_days", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_store_targets_store_target_sales_days_positive"),
        "store_targets",
        "sales_days IS NULL OR sales_days >= 1",
    )
    op.create_table(
        "incentive_inputs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("person_id", sa.String(length=64), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_incentive_inputs_tenant_id_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["people.tenant_id", "people.id"],
            name="fk_incentive_inputs_tenant_person",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incentive_inputs")),
        sa.CheckConstraint(
            "month BETWEEN 1 AND 12",
            name=op.f("ck_incentive_inputs_incentive_month_in_range"),
        ),
        sa.CheckConstraint(
            "version >= 1",
            name=op.f("ck_incentive_inputs_incentive_version_positive"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "person_id",
            "year",
            "month",
            "version",
            name="uq_incentive_person_month_version",
        ),
    )
    op.create_index(
        op.f("ix_incentive_inputs_tenant_id"),
        "incentive_inputs",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_incentive_inputs_tenant_id"), table_name="incentive_inputs"
    )
    op.drop_table("incentive_inputs")
    op.drop_constraint(
        op.f("ck_store_targets_store_target_sales_days_positive"),
        "store_targets",
        type_="check",
    )
    op.drop_column("store_targets", "sales_days")
    op.drop_column("sales_store_day", "sim_quantity")
