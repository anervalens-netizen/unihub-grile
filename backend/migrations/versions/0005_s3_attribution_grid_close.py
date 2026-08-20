"""S3 — sales attribution projections, salary master, holiday calendar,
month close audit chain and grid snapshot support.

The S3 slice adds five new tables without touching any S1/S2 schema:

* ``salary_master`` — effective-dated HR/payroll salary + ticket input.
  Composite-unique on ``(tenant_id, person_id, effective_from)``.
* ``holiday_calendars`` — versioned Romanian legal holiday marker.
  Read-only by the engine in S3 (no Pontaj/schedule effect).
* ``holiday_overrides`` — admin override flag + reason + actor id.
* ``month_close_events`` — append-only close/reopen audit chain. Every
  successful transition appends one row; previous state and revision are
  captured so the chain is reconstructable without mutating ``Month``.
* ``sales_person_day_projections`` — per-reversion projection of the
  attributed store-day credit. Materialised in the same transaction as
  the calendar CAS, keyed by ``(month, person, store, date, revision,
  generation)``.

All constraint names follow the shared ``NAMING_CONVENTION`` so
``alembic check`` reports zero drift against the ORM models.

Revision ID: e7f3a9b1c2d4
Revises: d4e6f8a0b2c4
Create Date: 2026-08-20 22:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "e7f3a9b1c2d4"
down_revision: str | None = "d4e6f8a0b2c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- salary_master -----------------------------------------------------
    op.create_table(
        "salary_master",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("person_id", sa.String(length=64), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("salary", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("tickets", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("flip", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
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
            name=op.f("fk_salary_master_tenant_id_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["people.tenant_id", "people.id"],
            name="fk_salary_master_tenant_person",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_salary_master")),
        sa.UniqueConstraint(
            "tenant_id",
            "person_id",
            "effective_from",
            name="uq_salary_master_window",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name=op.f("ck_salary_master_salary_master_dates_valid"),
        ),
    )
    op.create_index(
        op.f("ix_salary_master_tenant_id"), "salary_master", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_salary_master_tenant_person_dates",
        "salary_master",
        ["tenant_id", "person_id", "effective_from"],
        unique=False,
    )

    # --- holiday_calendars -------------------------------------------------
    op.create_table(
        "holiday_calendars",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
            name=op.f("fk_holiday_calendars_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_holiday_calendars")),
        sa.UniqueConstraint(
            "tenant_id",
            "version",
            "business_date",
            name="uq_holiday_calendar_version_date",
        ),
    )
    op.create_index(
        op.f("ix_holiday_calendars_tenant_id"),
        "holiday_calendars",
        ["tenant_id"],
        unique=False,
    )

    # --- holiday_overrides -------------------------------------------------
    op.create_table(
        "holiday_overrides",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
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
            name=op.f("fk_holiday_overrides_tenant_id_tenants"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_holiday_overrides")),
        sa.UniqueConstraint(
            "tenant_id",
            "version",
            "business_date",
            name="uq_holiday_override_version_date",
        ),
    )
    op.create_index(
        op.f("ix_holiday_overrides_tenant_id"),
        "holiday_overrides",
        ["tenant_id"],
        unique=False,
    )

    # --- month_close_events -----------------------------------------------
    op.create_table(
        "month_close_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("month_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("previous_state", sa.String(length=16), nullable=False),
        sa.Column("new_state", sa.String(length=16), nullable=False),
        sa.Column("revision_before", sa.Integer(), nullable=False),
        sa.Column("revision_after", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("blockers", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_month_close_events_tenant_id_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["month_id"],
            ["months.id"],
            name=op.f("fk_month_close_events_month_id_months"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_month_close_events")),
        sa.CheckConstraint(
            "action IN ('CLOSE', 'REOPEN')",
            name=op.f("ck_month_close_events_month_close_events_action_enum"),
        ),
        sa.CheckConstraint(
            "previous_state IN ('DRAFT', 'OPEN', 'CLOSED', 'REOPENED') "
            "AND new_state IN ('DRAFT', 'OPEN', 'CLOSED', 'REOPENED')",
            name=op.f("ck_month_close_events_month_close_events_states_enum"),
        ),
    )
    op.create_index(
        op.f("ix_month_close_events_tenant_id"),
        "month_close_events",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_month_close_events_month_id"),
        "month_close_events",
        ["month_id"],
        unique=False,
    )
    op.create_index(
        "ix_month_close_events_month",
        "month_close_events",
        ["tenant_id", "month_id", "occurred_at"],
        unique=False,
    )

    # --- sales_person_day_projections -------------------------------------
    op.create_table(
        "sales_person_day_projections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("month_id", sa.String(length=64), nullable=False),
        sa.Column("person_id", sa.String(length=64), nullable=False),
        sa.Column("store_id", sa.String(length=64), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("generation", sa.String(length=32), nullable=False),
        sa.Column("working_kind", sa.String(length=16), nullable=False),
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
            name=op.f("fk_sales_person_day_projections_tenant_id_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["month_id"],
            ["months.id"],
            name=op.f("fk_sales_person_day_projections_month_id_months"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "store_id"],
            ["stores.tenant_id", "stores.id"],
            name="fk_sales_person_day_projection_tenant_store",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["people.tenant_id", "people.id"],
            name="fk_sales_person_day_projection_tenant_person",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sales_person_day_projections")),
        sa.UniqueConstraint(
            "tenant_id",
            "month_id",
            "person_id",
            "store_id",
            "business_date",
            "revision",
            "generation",
            name="uq_sales_person_day_projection",
        ),
        sa.CheckConstraint(
            "working_kind IN ('NORMAL', 'EXTRA_HOME', 'EXTRA_OTHER')",
            name=op.f("ck_sales_person_day_projections_sales_person_day_projection_kind_enum"),
        ),
    )
    op.create_index(
        op.f("ix_sales_person_day_projections_tenant_id"),
        "sales_person_day_projections",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_sales_person_day_projections_month_id"),
        "sales_person_day_projections",
        ["month_id"],
        unique=False,
    )
    op.create_index(
        "ix_sales_person_day_projection_current",
        "sales_person_day_projections",
        ["tenant_id", "month_id", "revision", "business_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sales_person_day_projection_current",
        table_name="sales_person_day_projections",
    )
    op.drop_index(
        op.f("ix_sales_person_day_projections_month_id"),
        table_name="sales_person_day_projections",
    )
    op.drop_index(
        op.f("ix_sales_person_day_projections_tenant_id"),
        table_name="sales_person_day_projections",
    )
    op.drop_table("sales_person_day_projections")

    op.drop_index(
        "ix_month_close_events_month", table_name="month_close_events"
    )
    op.drop_index(
        op.f("ix_month_close_events_month_id"), table_name="month_close_events"
    )
    op.drop_index(
        op.f("ix_month_close_events_tenant_id"), table_name="month_close_events"
    )
    op.drop_table("month_close_events")

    op.drop_index(
        op.f("ix_holiday_overrides_tenant_id"), table_name="holiday_overrides"
    )
    op.drop_table("holiday_overrides")

    op.drop_index(
        op.f("ix_holiday_calendars_tenant_id"), table_name="holiday_calendars"
    )
    op.drop_table("holiday_calendars")

    op.drop_index(
        "ix_salary_master_tenant_person_dates", table_name="salary_master"
    )
    op.drop_index(
        op.f("ix_salary_master_tenant_id"), table_name="salary_master"
    )
    op.drop_table("salary_master")