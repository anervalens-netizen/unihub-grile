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

Composite tenant integrity
--------------------------

Every foreign key that points to ``stores`` or ``people`` is composite:
``(tenant_id, target_id) -> stores(tenant_id, id)`` (or ``people``). Because
``stores.id`` is the primary key, ``stores(tenant_id, id)`` is unique and
the composite FK is well-defined. The result is DB-enforced tenant integrity:
a row in tenant ``X`` cannot reference a store from tenant ``Y`` because the
composite columns would not match.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
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

    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        UniqueConstraint("tenant_id", "id", name="uq_users_tenant_id"),
    )


class ManagerScope(Base, TimestampMixin):
    """Effective-dated store scope for manager/TL calendar writes."""

    __tablename__ = "manager_scopes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    store_id: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="manager_scope_dates_valid",
        ),
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "store_id",
            "effective_from",
            name="uq_manager_scope_window",
        ),
        Index(
            "ix_manager_scopes_tenant_user_dates",
            "tenant_id",
            "user_id",
            "effective_from",
            "effective_to",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_manager_scopes_tenant_user",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "store_id"],
            ["stores.tenant_id", "stores.id"],
            name="fk_manager_scopes_tenant_store",
        ),
    )


class ScheduleImportContract(Base, TimestampMixin):
    """Single-use server-issued contract embedded in an XLSX Manifest."""

    __tablename__ = "schedule_import_contracts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    month_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("months.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    catalog_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_schedule_import_contracts_lookup",
            "tenant_id",
            "month_id",
            "user_id",
            "token_hash",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_schedule_import_contracts_tenant_user",
        ),
    )


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
        UniqueConstraint("tenant_id", "id", name="uq_stores_tenant_id"),
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
    home_store_id: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "internal_code", name="uq_people_tenant_internal"),
        UniqueConstraint("tenant_id", "id", name="uq_people_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "home_store_id"],
            ["stores.tenant_id", "stores.id"],
            name="fk_people_tenant_home_store",
        ),
    )


class StoreAssignment(Base, TimestampMixin):
    """Effective-dated belonging of a person to a store (history)."""

    __tablename__ = "store_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    person_id: Mapped[str] = mapped_column(String(64), nullable=False)
    store_id: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="store_assignment_dates_valid",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["people.tenant_id", "people.id"],
            name="fk_store_assignments_tenant_person",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "store_id"],
            ["stores.tenant_id", "stores.id"],
            name="fk_store_assignments_tenant_store",
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
    """One working assignment per store-day (or OFF/LEAVE row)."""

    __tablename__ = "site_day_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    month_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("months.id"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(String(64), nullable=False)
    person_id: Mapped[str] = mapped_column(String(64), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=DayStatus.WORKING)
    working_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="MANAGER_UI")

    __table_args__ = (
        Index(
            "uq_site_day_one_working",
            "tenant_id",
            "store_id",
            "business_date",
            unique=True,
            sqlite_where=text("status = 'WORKING'"),
            postgresql_where=text("status = 'WORKING'"),
        ),
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
        ForeignKeyConstraint(
            ["tenant_id", "store_id"],
            ["stores.tenant_id", "stores.id"],
            name="fk_site_day_assignments_tenant_store",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["people.tenant_id", "people.id"],
            name="fk_site_day_assignments_tenant_person",
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
    person_id: Mapped[str] = mapped_column(String(64), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "person_id", "business_date", name="uq_absence_person_day"),
        ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["people.tenant_id", "people.id"],
            name="fk_person_day_absences_tenant_person",
        ),
        CheckConstraint("status IN ('OFF', 'LEAVE')", name="absence_status_enum"),
    )


class PontajProjection(Base, TimestampMixin):
    """Complete read-only Pontaj row materialized for a calendar revision."""

    __tablename__ = "pontaj_projections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    month_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("months.id"), nullable=False, index=True
    )
    person_id: Mapped[str] = mapped_column(String(64), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    pause_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hours: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "month_id",
            "person_id",
            "business_date",
            "revision",
            name="uq_pontaj_projection_revision_day",
        ),
        Index(
            "ix_pontaj_projections_current",
            "tenant_id",
            "month_id",
            "revision",
            "business_date",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["people.tenant_id", "people.id"],
            name="fk_pontaj_projections_tenant_person",
        ),
        CheckConstraint("status IN ('WORKING', 'OFF', 'LEAVE')", name="pontaj_status_enum"),
    )


class SalesStoreDay(Base, TimestampMixin):
    """Immutable physical store-day sale, per connector generation."""

    __tablename__ = "sales_store_day"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(String(64), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    generation: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RON")
    source_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sim_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "store_id",
            "business_date",
            "generation",
            name="uq_sales_store_day_generation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "store_id"],
            ["stores.tenant_id", "stores.id"],
            name="fk_sales_store_day_tenant_store",
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
    store_id: Mapped[str] = mapped_column(String(64), nullable=False)
    person_id: Mapped[str] = mapped_column(String(64), nullable=False)
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
        ForeignKeyConstraint(
            ["tenant_id", "store_id"],
            ["stores.tenant_id", "stores.id"],
            name="fk_sales_person_day_tenant_store",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["people.tenant_id", "people.id"],
            name="fk_sales_person_day_tenant_person",
        ),
    )


class EpayObservation(Base, TimestampMixin):
    """A single read of an E-pay quantity from Google Sheets."""

    __tablename__ = "epay_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(String(64), nullable=False)
    person_id: Mapped[str] = mapped_column(String(64), nullable=False)
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
        Index(
            "ix_epay_observations_tenant_month",
            "tenant_id",
            "observed_at",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "store_id"],
            ["stores.tenant_id", "stores.id"],
            name="fk_epay_observations_tenant_store",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["people.tenant_id", "people.id"],
            name="fk_epay_observations_tenant_person",
        ),
    )


class GridCalculation(Base, TimestampMixin):
    """Snapshot of grid inputs and outputs for a store/person/month."""

    __tablename__ = "grid_calculations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    month_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("months.id"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(String(64), nullable=False)
    person_id: Mapped[str] = mapped_column(String(64), nullable=False)
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
        ForeignKeyConstraint(
            ["tenant_id", "store_id"],
            ["stores.tenant_id", "stores.id"],
            name="fk_grid_calculations_tenant_store",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["people.tenant_id", "people.id"],
            name="fk_grid_calculations_tenant_person",
        ),
    )


class SheetBinding(Base, TimestampMixin):
    """Permanent binding between a store and its Google Sheet."""

    __tablename__ = "sheet_bindings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(String(64), nullable=False)
    spreadsheet_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sheet_name_grila: Mapped[str] = mapped_column(String(64), nullable=False)
    sheet_name_pontaj: Mapped[str] = mapped_column(String(64), nullable=False)
    generation: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "store_id", name="uq_sheet_binding_tenant_store"),
        UniqueConstraint("spreadsheet_id", name="uq_sheet_binding_spreadsheet_id"),
        ForeignKeyConstraint(
            ["tenant_id", "store_id"],
            ["stores.tenant_id", "stores.id"],
            name="fk_sheet_bindings_tenant_store",
        ),
    )


class SheetProjectionRun(Base, TimestampMixin):
    """Status of a Google projection run (idempotent worker job output)."""

    __tablename__ = "sheet_projection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id"), nullable=False, index=True
    )
    store_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_success_generation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    generation: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "store_id"],
            ["stores.tenant_id", "stores.id"],
            name="fk_sheet_projection_runs_tenant_store",
        ),
    )


class ImportRun(Base, TimestampMixin):
    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    errors: Mapped[str] = mapped_column(Text, nullable=False, default="[]")


class ExportRun(Base, TimestampMixin):
    __tablename__ = "export_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    artifact_uri: Mapped[str | None] = mapped_column(String(256), nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id"), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_audit_tenant_entity", "tenant_id", "entity", "entity_id"),)


class OutboxJob(Base, TimestampMixin):
    __tablename__ = "outbox_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "idempotency_key", name="uq_outbox_tenant_kind_idempotency"),
        CheckConstraint(
            "kind IN ('FIXTURE_INGEST', 'TENANT_BOOTSTRAP', 'NOOP', "
            "'FIXTURE_INGEST_BY_TENANT', 'EXPORT_XLSX_STORE', 'EXPORT_XLSX_BULK', "
            "'EXPORT_PONTAJ_ONLY', 'GOOGLE_PROJECTION_STORE')",
            name="outbox_kind_enum",
        ),
        CheckConstraint("status IN ('PENDING', 'RUNNING', 'DONE', 'FAILED')", name="outbox_status_enum"),
    )


class StoreTarget(Base, TimestampMixin):
    __tablename__ = "store_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id"), nullable=False, index=True)
    store_id: Mapped[str] = mapped_column(String(64), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RON")
    sales_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        CheckConstraint("month BETWEEN 1 AND 12", name="store_target_month_in_range"),
        CheckConstraint("kind IN ('MONTHLY_SALES', 'MONTHLY_UNITS', 'MONTHLY_ATTACH')", name="store_target_kind_enum"),
        CheckConstraint("sales_days IS NULL OR sales_days >= 1", name="store_target_sales_days_positive"),
        UniqueConstraint("tenant_id", "store_id", "year", "month", "kind", "version", name="uq_store_target_window_version"),
        ForeignKeyConstraint(["tenant_id", "store_id"], ["stores.tenant_id", "stores.id"], name="fk_store_targets_tenant_store"),
    )


class SalaryMaster(Base, TimestampMixin):
    __tablename__ = "salary_master"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id"), nullable=False, index=True)
    person_id: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    salary: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    tickets: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    flip: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="HR_MASTER")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "person_id", "effective_from", name="uq_salary_master_window"),
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="salary_master_dates_valid"),
        ForeignKeyConstraint(["tenant_id", "person_id"], ["people.tenant_id", "people.id"], name="fk_salary_master_tenant_person"),
        Index("ix_salary_master_tenant_person_dates", "tenant_id", "person_id", "effective_from"),
    )


class IncentiveInput(Base, TimestampMixin):
    __tablename__ = "incentive_inputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id"), nullable=False, index=True)
    person_id: Mapped[str] = mapped_column(String(64), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RON")

    __table_args__ = (
        CheckConstraint("month BETWEEN 1 AND 12", name="incentive_month_in_range"),
        CheckConstraint("version >= 1", name="incentive_version_positive"),
        UniqueConstraint("tenant_id", "person_id", "year", "month", "version", name="uq_incentive_person_month_version"),
        ForeignKeyConstraint(["tenant_id", "person_id"], ["people.tenant_id", "people.id"], name="fk_incentive_inputs_tenant_person"),
    )


class HolidayCalendar(Base, TimestampMixin):
    __tablename__ = "holiday_calendars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id"), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "version", "business_date", name="uq_holiday_calendar_version_date"),
    )


class HolidayOverride(Base, TimestampMixin):
    __tablename__ = "holiday_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id"), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "version", "business_date", name="uq_holiday_override_version_date"),
    )


class MonthCloseEvent(Base):
    __tablename__ = "month_close_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id"), nullable=False, index=True)
    month_id: Mapped[str] = mapped_column(String(64), ForeignKey("months.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    previous_state: Mapped[str] = mapped_column(String(16), nullable=False)
    new_state: Mapped[str] = mapped_column(String(16), nullable=False)
    revision_before: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_after: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    blockers: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    previous_event_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_month_close_events_month", "tenant_id", "month_id", "occurred_at"),
        CheckConstraint("action IN ('CLOSE', 'REOPEN')", name="month_close_events_action_enum"),
        CheckConstraint(
            "previous_state IN ('DRAFT', 'OPEN', 'CLOSED', 'REOPENED') "
            "AND new_state IN ('DRAFT', 'OPEN', 'CLOSED', 'REOPENED')",
            name="month_close_events_states_enum",
        ),
    )


class SalesPersonDayProjection(Base, TimestampMixin):
    __tablename__ = "sales_person_day_projections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id"), nullable=False, index=True)
    month_id: Mapped[str] = mapped_column(String(64), ForeignKey("months.id"), nullable=False, index=True)
    person_id: Mapped[str] = mapped_column(String(64), nullable=False)
    store_id: Mapped[str] = mapped_column(String(64), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RON")
    generation: Mapped[str] = mapped_column(String(32), nullable=False)
    working_kind: Mapped[str] = mapped_column(String(16), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "month_id", "person_id", "store_id", "business_date", "revision", "generation", name="uq_sales_person_day_projection"),
        Index("ix_sales_person_day_projection_current", "tenant_id", "month_id", "revision", "business_date"),
        ForeignKeyConstraint(["tenant_id", "store_id"], ["stores.tenant_id", "stores.id"], name="fk_sales_person_day_projection_tenant_store"),
        ForeignKeyConstraint(["tenant_id", "person_id"], ["people.tenant_id", "people.id"], name="fk_sales_person_day_projection_tenant_person"),
        CheckConstraint("working_kind IN ('NORMAL', 'EXTRA_HOME', 'EXTRA_OTHER')", name="sales_person_day_projection_kind_enum"),
    )


__all__ = [
    "Tenant",
    "User",
    "ManagerScope",
    "ScheduleImportContract",
    "Store",
    "Person",
    "StoreAssignment",
    "Month",
    "SiteDayAssignment",
    "PersonDayAbsence",
    "PontajProjection",
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
    "StoreTarget",
    "SalaryMaster",
    "IncentiveInput",
    "HolidayCalendar",
    "HolidayOverride",
    "MonthCloseEvent",
    "SalesPersonDayProjection",
]
