"""SQLAlchemy ORM base and shared mixins.

All tables include ``tenant_id`` (multi-tenant boundary) and standard audit
columns. Soft delete is intentionally absent — business state is append-only
where it matters (audit, sales, assignments) and the application service
decides on visibility through ``status`` columns.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Naming convention keeps Alembic diffs stable.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base with the project naming convention."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


def tenant_id_column() -> Mapped[str]:
    """A model helper cannot return a column; models declare it directly.

    See ``models.py`` for the per-model ``tenant_id`` mapping.
    """
    raise RuntimeError("tenant_id is declared on each model directly; see repositories/models.py")


def to_dict(instance: Any) -> dict[str, Any]:
    """Return a JSON-safe mapping of an ORM instance."""

    from sqlalchemy import inspect

    mapper = inspect(instance).mapper
    out: dict[str, Any] = {}
    for col in mapper.columns:
        value = getattr(instance, col.key)
        if isinstance(value, datetime):
            out[col.key] = value.isoformat()
        else:
            out[col.key] = value
    return out


__all__ = ["Base", "TimestampMixin", "to_dict"]
