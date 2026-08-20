"""effective-dated manager scopes

Stage S2 adds the authorization data needed to restrict calendar and XLSX
schedule changes to manager/TL store scopes.  Scope rows are tenant-safe and
carry an inclusive effective date window.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c3a1b7e2d4f6"
down_revision: str | None = "b9fbb01f8cd0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_users_tenant_id", "users", ["tenant_id", "id"])
    op.create_table(
        "manager_scopes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("store_id", sa.String(length=64), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
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
            "effective_to IS NULL OR effective_to >= effective_from",
            name="manager_scope_dates_valid",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_manager_scopes_tenant_id_tenants"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_manager_scopes_tenant_user",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "store_id"],
            ["stores.tenant_id", "stores.id"],
            name="fk_manager_scopes_tenant_store",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_manager_scopes"),
        sa.UniqueConstraint(
            "tenant_id",
            "user_id",
            "store_id",
            "effective_from",
            name="uq_manager_scope_window",
        ),
    )
    op.create_index("ix_manager_scopes_tenant_id", "manager_scopes", ["tenant_id"], unique=False)
    op.create_index(
        "ix_manager_scopes_tenant_user_dates",
        "manager_scopes",
        ["tenant_id", "user_id", "effective_from", "effective_to"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_manager_scopes_tenant_user_dates", table_name="manager_scopes")
    op.drop_index("ix_manager_scopes_tenant_id", table_name="manager_scopes")
    op.drop_table("manager_scopes")
    op.drop_constraint("uq_users_tenant_id", "users", type_="unique")
