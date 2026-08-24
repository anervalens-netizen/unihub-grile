"""Add Retail organization labels to the Grile store catalog.

Revision ID: d4e6f8a0b2c4
Revises: b7c9d1e3f5a7
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e6f8a0b2c4"
down_revision: str | None = "b7c9d1e3f5a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("stores", sa.Column("regional", sa.String(length=128), nullable=True))
    op.add_column("stores", sa.Column("asm", sa.String(length=128), nullable=True))


def downgrade() -> None:
    op.drop_column("stores", "asm")
    op.drop_column("stores", "regional")
