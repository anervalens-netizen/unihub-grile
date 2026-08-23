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
    schema_version: str | None
    expected_schema_version: str
    schema_current: bool
    worker_enabled: bool
    stale_running_jobs: int | None
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


class CloseIn(BaseModel):
    expected_revision: int


class ReopenIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class BlockerOut(BaseModel):
    code: str
    store_id: str | None = None
    person_id: str | None = None
    business_date: date | None = None
    message: str


class CloseOutcomeOut(BaseModel):
    month_id: str
    revision: int
    new_state: MonthState
    audit_event_id: int | None
    blockers: list[BlockerOut]


class CloseEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    month_id: str
    event_type: str
    actor_id: str
    actor_role: str
    reason: str | None
    previous_state: str
    new_state: str
    revision: int
    previous_digest: str | None
    event_digest: str
    created_at: str


class CalendarCellEditIn(BaseModel):
    person_id: str
    business_date: date
    status: DayStatus
    store_id: str | None = None
    working_kind: WorkingKind | None = None


class ProgramCellOut(BaseModel):
    business_date: date
    person_id: str
    store_id: str | None
    status: DayStatus
    working_kind: WorkingKind | None
    hours: Decimal
    locked: bool


class ProgramRowOut(BaseModel):
    row_id: str
    label: str
    home_store_id: str | None
    cells: list[ProgramCellOut]


class ProgramGridOut(BaseModel):
    month_id: str
    year: int
    month: int
    revision: int
    dates: list[date]
    rows: list[ProgramRowOut]
    legend: list[str]


class ProgramChoiceOut(BaseModel):
    person_id: str
    display_name: str
    home_store_id: str
    allowed_store_ids: list[str]
    working_kinds: list[WorkingKind]


class ProgramChoicesOut(BaseModel):
    month_id: str
    business_date: date
    store_id: str
    choices: list[ProgramChoiceOut]


class OverviewKpiOut(BaseModel):
    active_stores: int
    active_people: int
    working_assignments: int
    coverage_gaps: int
    exceptions: int
    grid_people: int
    epay_fresh: bool


class OverviewManagerRowOut(BaseModel):
    manager_id: str
    manager_name: str
    store_count: int
    working_assignments: int
    coverage_gaps: int
    exception_count: int


class OverviewNeedsAttentionOut(BaseModel):
    code: str
    severity: str
    store_id: str | None
    person_id: str | None
    business_date: date | None
    message: str


class OverviewOut(BaseModel):
    month_id: str
    year: int
    month: int
    state: str
    revision: int
    rule_pack_version: str
    kpis: OverviewKpiOut
    managers: list[OverviewManagerRowOut]
    needs_attention: list[OverviewNeedsAttentionOut]


class ExceptionOut(BaseModel):
    code: str
    severity: str
    store_id: str | None
    person_id: str | None
    business_date: date | None
    message: str
    source: str


class ChecklistItemOut(BaseModel):
    code: str
    blocking: bool
    message: str
    store_id: str | None = None
    person_id: str | None = None
    business_date: date | None = None


class CloseChecklistOut(BaseModel):
    month_id: str
    revision: int
    state: str
    blockers: list[ChecklistItemOut]
    generated_at: str
    export_summary: list[dict[str, object]]
    job_summary: list[dict[str, object]]
    expected_revision: int


class ReopenWithReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class WorkerJobDiagnosticOut(BaseModel):
    id: int
    kind: str
    status: str
    attempts: int
    run_after: str
    locked_at: str | None
    locked_by: str | None
    last_error: str | None
    created_at: str
    updated_at: str


class WorkerDiagnosticsOut(BaseModel):
    active: list[WorkerJobDiagnosticOut]
    recent_terminal: list[WorkerJobDiagnosticOut]


class SheetBindingCreateIn(BaseModel):
    store_id: str
    spreadsheet_id: str = Field(min_length=1, max_length=256)


class SheetBindingOut(BaseModel):
    store_id: str
    configured: bool
    sheet_identity_hint: str | None
    created_at: str | None
    updated_at: str | None


class SheetBindingCanaryOut(BaseModel):
    store_id: str
    configured: bool
    sheet_identity_hint: str
    provider: str
    live_mutation_gate: bool


class SheetReconciliationOut(BaseModel):
    store_id: str
    month_id: str
    available: bool
    generation: str | None = None
    format_version: str | None = None
    revision: int | None = None
    rule_pack_version: str | None = None
    projected_at: str | None = None
    verification_mode: str | None = None
    verified: bool | None = None
    grila_rows: int | None = None
    pontaj_rows: int | None = None
    grila_checksum: str | None = None
    pontaj_checksum: str | None = None
    projection_checksum: str | None = None


class EpayGoogleReadbackOut(BaseModel):
    month_id: str
    store_id: str
    revision: int
    observation_count: int
    person_count: int
    observed_at: str
    fresh: bool


__all__ = [name for name in globals() if not name.startswith("_")]
