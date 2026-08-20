"""single-use XLSX contracts and persistent Pontaj projections

S2 remediation binds every workbook to a server-issued, single-use contract
and materializes the complete Pontaj projection for every calendar revision.
Constraint names follow the shared NAMING_CONVENTION so ``alembic check``
reports zero drift against the ORM models.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d4e6f8a0b2c4"
down_revision: str | None = "c3a1b7e2d4f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedule_import_contracts",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("month_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("catalog_hash", sa.String(length=64), nullable=False),
        sa.Column("scope_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
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
            name=op.f("fk_schedule_import_contracts_tenant_id_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["month_id"],
            ["months.id"],
            name=op.f("fk_schedule_import_contracts_month_id_months"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_schedule_import_contracts_tenant_user",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedule_import_contracts")),
        sa.UniqueConstraint(
            "token_hash", name="uq_schedule_import_contracts_token_hash"
        ),
    )
    op.create_index(
        op.f("ix_schedule_import_contracts_tenant_id"),
        "schedule_import_contracts",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_schedule_import_contracts_month_id"),
        "schedule_import_contracts",
        ["month_id"],
        unique=False,
    )
    op.create_index(
        "ix_schedule_import_contracts_lookup",
        "schedule_import_contracts",
        ["tenant_id", "month_id", "user_id", "token_hash"],
        unique=False,
    )

    op.create_table(
        "pontaj_projections",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("month_id", sa.String(length=64), nullable=False),
        sa.Column("person_id", sa.String(length=64), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("pause_minutes", sa.Integer(), nullable=False),
        sa.Column("hours", sa.Numeric(precision=8, scale=2), nullable=False),
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
        sa.CheckConstraint(
            "status IN ('WORKING', 'OFF', 'LEAVE')",
            name=op.f("ck_pontaj_projections_pontaj_status_enum"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_pontaj_projections_tenant_id_tenants"),
        ),
        sa.ForeignKeyConstraint(
            ["month_id"],
            ["months.id"],
            name=op.f("fk_pontaj_projections_month_id_months"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["people.tenant_id", "people.id"],
            name="fk_pontaj_projections_tenant_person",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pontaj_projections")),
        sa.UniqueConstraint(
            "tenant_id",
            "month_id",
            "person_id",
            "business_date",
            "revision",
            name="uq_pontaj_projection_revision_day",
        ),
    )
    op.create_index(
        op.f("ix_pontaj_projections_tenant_id"), "pontaj_projections", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_pontaj_projections_month_id"), "pontaj_projections", ["month_id"], unique=False
    )
    op.create_index(
        "ix_pontaj_projections_current",
        "pontaj_projections",
        ["tenant_id", "month_id", "revision", "business_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_pontaj_projections_current", table_name="pontaj_projections")
    op.drop_index(
        op.f("ix_pontaj_projections_month_id"), table_name="pontaj_projections"
    )
    op.drop_index(
        op.f("ix_pontaj_projections_tenant_id"), table_name="pontaj_projections"
    )
    op.drop_table("pontaj_projections")
    op.drop_index(
        "ix_schedule_import_contracts_lookup", table_name="schedule_import_contracts"
    )
    op.drop_index(
        op.f("ix_schedule_import_contracts_month_id"), table_name="schedule_import_contracts"
    )
    op.drop_index(
        op.f("ix_schedule_import_contracts_tenant_id"), table_name="schedule_import_contracts"
    )
    op.drop_table("schedule_import_contracts")
