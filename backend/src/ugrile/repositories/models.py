"""SQLAlchemy ORM models for the S1 foundation schema.

All tables include ``tenant_id``. The ``site_day_assignments`` table holds
two partial unique indexes that enforce AC-02 at the DB transaction boundary:

* ``uq_site_day_one_working`` — at most one ``WORKING`` row per
  ``(tenant_id, store_id, business_date)``.
* ``uq_person_day_one_working`` — at most one ``WORKING`` row per
  ``(tenant_id, person_id, business_date)``.

These are partial indexes; OFF and LEAVE rows do not collide. The same checks
are applied in pure form by ``ugrile.domain.calendar`` so the API can fail
fast with a precise error message.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..domain.enums import DayStatus, MonthState, RoleName
from .base import Base, TimestampMixin


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=RoleName.READONLY)

    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),)


class Store(Base, TimestampMixin):
    __tablename__ = "stores"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    company_code: Mapped[str] = mapped_column(String(32), nullable=False)
    internal_code: Mapped[str] = mapped_column(String(32), nullable=False)
    external_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "internal_code", name="uq_stores_tenant_internal"),
    )


class Person(Base, TimestampMixin):
    __tablename__ = "people"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    internal_code: Mapped[str] = mapped_column(String(32), nullable=False)
    external_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    home_store_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("stores.id"), nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "internal_code", name="uq_people_tenant_internal"),
    )


class StoreAssignment(Base, TimestampMixin):
    """Effective-dated belonging of a person to a store (history)."""

    __tablename__ = "store_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    person_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("people.id"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("stores.id"), nullable=False, index=True
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="store_assignment_dates_valid",
        ),
        Index(
            "ix_store_assignments_person_window",
            "tenant_id",
            "person_id",
            "effective_from",
        ),
    )


class Month(Base, TimestampMixin):
    __tablename__ = "months"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default=MonthState.DRAFT)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    closed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "year", "month", name="uq_months_tenant_year_month"),
        CheckConstraint("month BETWEEN 1 AND 12", name="month_in_range"),
        CheckConstraint("revision >= 0", name="revision_non_negative"),
    )


class SiteDayAssignment(Base, TimestampMixin):
    """One working assignment per store-day (or OFF/LEAVE row).

    Rows whose ``status`` is ``WORKING`` participate in the AC-02 partial
    unique indexes. OFF and LEAVE rows are still useful for the calendar view
    and audit history but must not collide on the working invariants.
    """

    __tablename__ = "site_day_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    month_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("months.id"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("stores.id"), nullable=False, index=True
    )
    person_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("people.id"), nullable=False, index=True
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=DayStatus.WORKING)
    working_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="MANAGER_UI")

    __table_args__ = (
        # AC-02 partial unique index #1: one working agent per store-day.
        Index(
            "uq_site_day_one_working",
            "tenant_id",
            "store_id",
            "business_date",
            unique=True,
            sqlite_where=text("status = 'WORKING'"),
            postgresql_where=text("status = 'WORKING'"),
        ),
        # AC-02 partial unique index #2: one working store per agent-day.
        Index(
            "uq_person_day_one_working",
            "tenant_id",
            "person_id",
            "business_date",
            unique=True,
            sqlite_where=text("status = 'WORKING'"),
            postgresql_where=text("status = 'WORKING'"),
        ),
        Index(
            "ix_site_day_assignments_month_store",
            "month_id",
            "store_id",
            "business_date",
        ),
        CheckConstraint(
            "status IN ('WORKING', 'OFF', 'LEAVE')",
            name="status_enum",
        ),
        CheckConstraint(
            "(status = 'WORKING' AND working_kind IS NOT NULL) OR "
            "(status <> 'WORKING' AND working_kind IS NULL)",
            name="working_kind_only_when_working",
        ),
        CheckConstraint(
            "working_kind IS NULL OR working_kind IN ('NORMAL', 'EXTRA_HOME', 'EXTRA_OTHER')",
            name="working_kind_enum",
        ),
    )


class PersonDayAbsence(Base, TimestampMixin):
    """An OFF/LEAVE row that does NOT occupy store coverage."""

    __tablename__ = "person_day_absences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    month_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("months.id"), nullable=False, index=True
    )
    person_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("people.id"), nullable=False, index=True
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "person_id", "business_date", name="uq_absence_person_day"),
        CheckConstraint("status IN ('OFF', 'LEAVE')", name="absence_status_enum"),
    )


class SalesStoreDay(Base, TimestampMixin):
    """Immutable physical store-day sale, per connector generation."""

    __tablename__ = "sales_store_day"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("stores.id"), nullable=False, index=True
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    generation: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RON")
    source_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "store_id",
            "business_date",
            "generation",
            name="uq_sales_store_day_generation",
        ),
    )


class SalesPersonDay(Base, TimestampMixin):
    """Projection of store-day credit to the calendar-assigned person."""

    __tablename__ = "sales_person_day"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    month_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("months.id"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("stores.id"), nullable=False, index=True
    )
    person_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("people.id"), nullable=False, index=True
    )
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RON")
    generation: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "person_id",
            "store_id",
            "business_date",
            "generation",
            name="uq_sales_person_day_generation",
        ),
    )


class EpayObservation(Base, TimestampMixin):
    """A single read of an E-pay quantity from Google Sheets.

    Valid observations are 0..10 (integers). Invalid observations are kept in
    the audit log for forensics, but the engine only consumes the latest valid
    row per ``(tenant_id, store_id, person_id, category, generation)``.
    """

    __tablename__ = "epay_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("stores.id"), nullable=False, index=True
    )
    person_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("people.id"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("category IN ('UNDER_50', 'AT_OR_OVER_50')", name="epay_category_enum"),
        CheckConstraint(
            "value IS NULL OR (value >= 0 AND value <= 10)",
            name="epay_value_range",
        ),
    )


class GridCalculation(Base, TimestampMixin):
    """Snapshot of grid inputs and outputs for a store/person/month.

    The actual formula is filled in S3 (Mobiup rule pack). At S1 only the row
    shape exists so subsequent stages can plug in deterministic rules without
    a schema change.
    """

    __tablename__ = "grid_calculations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    month_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("months.id"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("stores.id"), nullable=False, index=True
    )
    person_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("people.id"), nullable=False, index=True
    )
    rule_pack_version: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outputs_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "month_id",
            "store_id",
            "person_id",
            "rule_pack_version",
            "revision",
            name="uq_grid_calc_window",
        ),
    )


class SheetBinding(Base, TimestampMixin):
    """Permanent binding between a store and its Google Sheet."""

    __tablename__ = "sheet_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("stores.id"), nullable=False, index=True
    )
    spreadsheet_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sheet_name_grila: Mapped[str] = mapped_column(String(64), nullable=False)
    sheet_name_pontaj: Mapped[str] = mapped_column(String(64), nullable=False)
    generation: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (UniqueConstraint("store_id", name="uq_sheet_binding_store"),)


class SheetProjectionRun(Base, TimestampMixin):
    """Status of a Google projection run (idempotent worker job output)."""

    __tablename__ = "sheet_projection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("stores.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_success_generation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ImportRun(Base, TimestampMixin):
    """Excel/connector import run record."""

    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    errors: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class ExportRun(Base, TimestampMixin):
    """Excel export run record."""

    __tablename__ = "export_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    artifact_uri: Mapped[str | None] = mapped_column(String(256), nullable=True)


class AuditEvent(Base):
    """Append-only audit log for business and administrative changes."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_audit_tenant_entity", "tenant_id", "entity", "entity_id"),)


class OutboxJob(Base, TimestampMixin):
    """Typed job row consumed by the single durable worker.

    The payload is JSON; ``kind`` selects the handler. ``idempotency_key`` lets
    the worker safely retry without producing duplicates.
    """

    __tablename__ = "outbox_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_outbox_idempotency"),
        CheckConstraint(
            "kind IN ('FIXTURE_INGEST', 'TENANT_BOOTSTRAP', 'NOOP')",
            name="outbox_kind_enum",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'DONE', 'FAILED')",
            name="outbox_status_enum",
        ),
    )


__all__ = [
    "Tenant",
    "User",
    "Store",
    "Person",
    "StoreAssignment",
    "Month",
    "SiteDayAssignment",
    "PersonDayAbsence",
    "SalesStoreDay",
    "SalesPersonDay",
    "EpayObservation",
    "GridCalculation",
    "SheetBinding",
    "SheetProjectionRun",
    "ImportRun",
    "ExportRun",
    "AuditEvent",
    "OutboxJob",
]
