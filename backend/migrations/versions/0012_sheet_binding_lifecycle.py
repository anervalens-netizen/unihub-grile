"""Enforce one permanent Google spreadsheet binding per store.

Revision ID: 9a1c3e5f7b2d
Revises: f6b8d0e2a4c6
Create Date: 2026-08-23 16:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "9a1c3e5f7b2d"
down_revision: str | None = "f6b8d0e2a4c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_sheet_binding_spreadsheet_id",
        "sheet_bindings",
        ["spreadsheet_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_sheet_binding_spreadsheet_id",
        "sheet_bindings",
        type_="unique",
    )
