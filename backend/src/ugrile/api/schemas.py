"""Pydantic request/response models for the API layer.

These types intentionally mirror the persisted shape closely. Domain rules
live in :mod:`ugrile.domain`; the API only translates HTTP into the right
service call.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import DayStatus, MonthState, WorkingKind


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
    expected_revision: int | None = Field(
        default=None,
        description="Optional CAS guard. If provided, the write is rejected when the month revision is older.",
    )


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


__all__ = [
    "AssignmentCreate",
    "AssignmentOut",
    "ConflictOut",
    "CoverageReport",
    "HealthReport",
    "IngestRequest",
    "IngestResult",
    "JobOut",
    "MonthOut",
    "PersonOut",
    "StoreOut",
    "TenantOut",
]
