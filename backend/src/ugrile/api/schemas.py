"""Pydantic request/response models for the API layer.

These types intentionally mirror the persisted shape closely. Domain rules
live in :mod:`ugrile.domain`; the API only translates HTTP into the right
service call.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import (
    CloseBlockerCode,
    DayStatus,
    MonthState,
    WorkingKind,
)


class HealthReport(BaseModel):
    status: Literal["ok", "degraded", "down"]
    database: bool
    schema_version: str
    app_version: str


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    timezone: str
    is_active: bool


class StoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    company_code: str
    internal_code: str
    external_code: str | None
    name: str
    is_active: bool


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    internal_code: str
    external_code: str | None
    display_name: str
    home_store_id: str
    is_active: bool


class MonthOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    year: int
    month: int
    state: MonthState
    revision: int
    closed_at: str | None = None


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    month_id: str
    store_id: str
    person_id: str
    business_date: date
    status: DayStatus
    working_kind: WorkingKind | None
    revision: int
    source: str


class AssignmentCreate(BaseModel):
    month_id: str
    store_id: str
    person_id: str
    business_date: date
    working_kind: WorkingKind
    expected_revision: int | None = Field(default=None)


class CalendarChangeIn(BaseModel):
    person_id: str
    business_date: date
    status: DayStatus
    store_id: str | None = None
    working_kind: WorkingKind | None = None


class CalendarApplyIn(BaseModel):
    expected_revision: int
    changes: list[CalendarChangeIn]


class CalendarProjectionOut(BaseModel):
    month_id: str
    revision: int
    assignment_count: int
    person_calendar_count: int
    coverage_count: int
    pontaj_count: int


class SchedulePreviewOut(BaseModel):
    base_revision: int
    changes: int
    errors: list[dict[str, object]]
    warnings: list[dict[str, object]]


class PontajRowOut(BaseModel):
    person_id: str
    business_date: date
    status: DayStatus
    start_time: time | None
    end_time: time | None
    pause_minutes: int
    hours: Decimal


class PontajPersonTotalsOut(BaseModel):
    person_id: str
    working_days: int
    leave_days: int
    off_days: int
    total_hours: Decimal


class PontajMonthOut(BaseModel):
    month_id: str
    revision: int
    rows: list[PontajRowOut]
    totals: list[PontajPersonTotalsOut]


class ConflictOut(BaseModel):
    code: str
    message: str
    store_id: str | None = None
    person_id: str | None = None
    business_date: str | None = None
    person_ids: list[str] | None = None
    store_ids: list[str] | None = None


class CoverageReport(BaseModel):
    month_id: str
    conflicts: list[ConflictOut]


class IngestResult(BaseModel):
    tenant: str
    generation: str
    stores: int
    people: int
    sales: int


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    kind: str
    status: str
    idempotency_key: str
    attempts: int
    last_error: str | None
    payload: str


class IngestRequest(BaseModel):
    tenant_token: str = Field(default="fixture", min_length=1, max_length=64)
    enqueue: bool = Field(
        default=False,
        description="When true, the API enqueues a background job instead of running inline.",
    )


class AttributionRowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    person_id: str
    store_id: str
    business_date: date
    amount: Decimal
    currency: str
    generation: str
    working_kind: WorkingKind
    revision: int


class AttributionMonthOut(BaseModel):
    month_id: str
    revision: int
    total_rows: int
    company_total: Decimal
    rows: list[AttributionRowOut]
    anomalies: list[dict[str, object]]


class SalaryUpsertIn(BaseModel):
    person_id: str = Field(min_length=1)
    effective_from: date
    effective_to: date | None = None
    salary: Decimal
    tickets: Decimal
    flip: Decimal = Decimal("0")
    source: str = "HR_MASTER"
    notes: str | None = None


class SalaryMasterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    person_id: str
    effective_from: date
    effective_to: date | None
    salary: Decimal
    tickets: Decimal
    flip: Decimal
    source: str
    notes: str | None = None


class GridCalculationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    month_id: str
    store_id: str
    person_id: str
    rule_pack_version: str
    revision: int
    inputs_hash: str
    outputs_hash: str
    payload: str


class GridComputeOut(BaseModel):
    month_id: str
    revision: int
    rule_pack_version: str
    snapshots: list[GridCalculationOut]


class HolidayMarkerOut(BaseModel):
    """One versioned Romanian legal holiday marker for a business date.

    ``override_active`` / ``override_reason`` are ``None`` when no admin
    override exists for the date. Informational only: the marker never
    changes schedule, Pontaj, target or pay (docs/MOBIUP_RULE_PACK.md §9).
    """

    version: str
    business_date: date
    label: str
    is_active: bool
    override_active: bool | None = None
    override_reason: str | None = None


class HolidayCalendarUpsertIn(BaseModel):
    version: str = Field(min_length=1, max_length=64)
    business_date: date
    label: str = Field(min_length=1, max_length=128)
    is_active: bool = True


class HolidayOverrideIn(BaseModel):
    version: str = Field(min_length=1, max_length=64)
    business_date: date
    is_active: bool
    reason: str = Field(min_length=4, max_length=512)


class HolidayMonthOut(BaseModel):
    month_id: str
    markers: list[HolidayMarkerOut]


class BlockerOut(BaseModel):
    code: CloseBlockerCode
    store_id: str | None
    person_id: str | None
    business_date: date | None
    message: str


class CloseIn(BaseModel):
    expected_revision: int | None = None


class CloseOutcomeOut(BaseModel):
    month_id: str
    revision: int
    new_state: MonthState
    audit_event_id: int
    blockers: list[BlockerOut]


class ReopenIn(BaseModel):
    reason: str = Field(min_length=4, max_length=512)


class CloseEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    month_id: str
    action: str
    previous_state: str
    new_state: str
    revision_before: int
    revision_after: int
    actor_id: str
    reason: str | None
    blockers: str


__all__ = [
    "AssignmentCreate",
    "AssignmentOut",
    "AttributionMonthOut",
    "AttributionRowOut",
    "BlockerOut",
    "CalendarApplyIn",
    "CalendarChangeIn",
    "CalendarProjectionOut",
    "CloseEventOut",
    "CloseIn",
    "CloseOutcomeOut",
    "ConflictOut",
    "CoverageReport",
    "GridCalculationOut",
    "GridComputeOut",
    "HealthReport",
    "HolidayCalendarUpsertIn",
    "HolidayMarkerOut",
    "HolidayMonthOut",
    "HolidayOverrideIn",
    "IngestRequest",
    "IngestResult",
    "JobOut",
    "MonthOut",
    "PersonOut",
    "PontajMonthOut",
    "PontajPersonTotalsOut",
    "PontajRowOut",
    "ReopenIn",
    "SalaryMasterOut",
    "SalaryUpsertIn",
    "SchedulePreviewOut",
    "StoreOut",
    "TenantOut",
]
