"""Grile-owned versioned DTOs for the future Retail adapter.

The module contains no Retail imports and performs no external I/O. It defines
only the semantic data boundary that a fixture adapter and a future Retail
adapter must both satisfy.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

RETAIL_GRILE_SCHEMA_V1 = "retail-grile.v1"
_MONTH_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")


def _non_empty(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _month(value: str) -> str:
    if not _MONTH_RE.fullmatch(value):
        raise ValueError("month must use YYYY-MM")
    return value


class RetailGenerationV1(BaseModel):
    """Exact authority heads that identify one accepted Retail snapshot."""

    sales_hash: str
    sales_revision: int = Field(ge=0)
    campaign_revision: int = Field(ge=0)
    cutoff_date: date
    generated_at: datetime

    @field_validator("sales_hash")
    @classmethod
    def _sales_hash(cls, value: str) -> str:
        return _non_empty(value, field_name="sales_hash")

    @field_validator("generated_at")
    @classmethod
    def _aware_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        return value


class RetailStoreV1(BaseModel):
    external_store_id: str
    display_name: str
    company_code: str
    is_active: bool = True

    @field_validator("external_store_id", "display_name", "company_code")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _non_empty(value, field_name="store field")


class RetailPersonV1(BaseModel):
    """Minimal person facts for the requested snapshot period.

    A future adapter must resolve exactly one defensible payroll/home store for
    the person in the requested period before constructing this DTO. It must
    not guess when Retail contains ambiguous multi-store evidence.
    """

    external_person_id: str
    display_name: str | None = None
    home_store_external_id: str
    is_active: bool = True

    @field_validator("external_person_id", "home_store_external_id")
    @classmethod
    def _required_identity(cls, value: str) -> str:
        return _non_empty(value, field_name="person identity")

    @field_validator("display_name")
    @classmethod
    def _optional_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class RetailManagerScopeV1(BaseModel):
    """Effective Retail organization assignment independent of UI role labels."""

    manager_key: str
    store_external_id: str
    regional_key: str | None = None
    valid_from_month: str
    valid_to_month: str | None = None

    @field_validator("manager_key", "store_external_id")
    @classmethod
    def _required_identity(cls, value: str) -> str:
        return _non_empty(value, field_name="scope identity")

    @field_validator("regional_key")
    @classmethod
    def _optional_regional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("valid_from_month")
    @classmethod
    def _valid_from(cls, value: str) -> str:
        return _month(value)

    @field_validator("valid_to_month")
    @classmethod
    def _valid_to(cls, value: str | None) -> str | None:
        return _month(value) if value is not None else None

    @model_validator(mode="after")
    def _ordered_interval(self) -> RetailManagerScopeV1:
        if self.valid_to_month is not None and self.valid_to_month < self.valid_from_month:
            raise ValueError("valid_to_month must be >= valid_from_month")
        return self


class RetailSalesStoreDayV1(BaseModel):
    external_store_id: str
    business_date: date
    amount: Decimal = Field(ge=0)
    currency: str = "RON"
    sim_quantity: int | None = Field(default=None, ge=0)

    @field_validator("external_store_id", "currency")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _non_empty(value, field_name="sales field")


class RetailTargetV1(BaseModel):
    external_store_id: str
    year: int = Field(ge=2000, le=2200)
    month: int = Field(ge=1, le=12)
    kind: Literal["MONTHLY_SALES", "MONTHLY_UNITS", "MONTHLY_ATTACH"]
    amount: Decimal = Field(ge=0)
    currency: str = "RON"
    sales_days: int | None = Field(default=None, ge=1, le=31)

    @field_validator("external_store_id", "currency")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _non_empty(value, field_name="target field")


class RetailIncentiveV1(BaseModel):
    external_person_id: str
    year: int = Field(ge=2000, le=2200)
    month: int = Field(ge=1, le=12)
    amount: Decimal = Field(ge=0)
    currency: str = "RON"
    authority_status: str | None = None

    @field_validator("external_person_id", "currency")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _non_empty(value, field_name="incentive field")

    @field_validator("authority_status")
    @classmethod
    def _optional_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class RetailPayrollInputV1(BaseModel):
    """Generic future external payroll input; absence is never implicit zero.

    ``amount`` intentionally allows signed values: a future authoritative
    payroll source may express corrections/adjustments. Individual input kinds
    can impose a narrower sign contract in a later schema without silently
    coercing a missing value to zero.
    """

    external_person_id: str
    year: int = Field(ge=2000, le=2200)
    month: int = Field(ge=1, le=12)
    input_kind: str
    amount: Decimal
    currency: str = "RON"

    @field_validator("external_person_id", "input_kind", "currency")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _non_empty(value, field_name="payroll input field")


class RetailSnapshotV1(BaseModel):
    """One complete, generation-pinned Retail snapshot for a Grile period."""

    schema_version: Literal["retail-grile.v1"] = "retail-grile.v1"
    tenant_id: str
    timezone: str
    period: str
    generation: RetailGenerationV1
    complete: Literal[True] = True
    stores: list[RetailStoreV1]
    people: list[RetailPersonV1]
    manager_scopes: list[RetailManagerScopeV1] = Field(default_factory=list)
    sales_store_day: list[RetailSalesStoreDayV1] = Field(default_factory=list)
    targets: list[RetailTargetV1] = Field(default_factory=list)
    incentives: list[RetailIncentiveV1] = Field(default_factory=list)
    payroll_inputs: list[RetailPayrollInputV1] = Field(default_factory=list)

    @field_validator("tenant_id")
    @classmethod
    def _tenant(cls, value: str) -> str:
        return _non_empty(value, field_name="tenant_id")

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        normalized = _non_empty(value, field_name="timezone")
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return normalized

    @field_validator("period")
    @classmethod
    def _period(cls, value: str) -> str:
        return _month(value)

    @model_validator(mode="after")
    def _snapshot_consistency(self) -> RetailSnapshotV1:
        if self.generation.cutoff_date.strftime("%Y-%m") != self.period:
            raise ValueError("generation cutoff_date must fall inside snapshot period")

        store_ids = [row.external_store_id for row in self.stores]
        if not store_ids:
            raise ValueError("snapshot requires at least one store")
        if len(store_ids) != len(set(store_ids)):
            raise ValueError("duplicate store identity")
        known_stores = set(store_ids)

        person_ids = [row.external_person_id for row in self.people]
        if len(person_ids) != len(set(person_ids)):
            raise ValueError("duplicate person identity")
        known_people = set(person_ids)
        for row in self.people:
            if row.home_store_external_id not in known_stores:
                raise ValueError("person references an unknown home store")

        sales_keys = [(row.external_store_id, row.business_date) for row in self.sales_store_day]
        if len(sales_keys) != len(set(sales_keys)):
            raise ValueError("duplicate store/day sales row")
        for row in self.sales_store_day:
            if row.external_store_id not in known_stores:
                raise ValueError("sales row references an unknown store")
            if row.business_date.strftime("%Y-%m") != self.period:
                raise ValueError("sales row falls outside snapshot period")
            if row.business_date > self.generation.cutoff_date:
                raise ValueError("sales row exceeds accepted cutoff_date")

        scope_keys = [
            (row.manager_key, row.store_external_id, row.valid_from_month, row.valid_to_month)
            for row in self.manager_scopes
        ]
        if len(scope_keys) != len(set(scope_keys)):
            raise ValueError("duplicate manager scope row")
        for row in self.manager_scopes:
            if row.store_external_id not in known_stores:
                raise ValueError("manager scope references an unknown store")

        target_keys = [(row.external_store_id, row.kind) for row in self.targets]
        if len(target_keys) != len(set(target_keys)):
            raise ValueError("duplicate target identity")
        for row in self.targets:
            if row.external_store_id not in known_stores:
                raise ValueError("target references an unknown store")
            if f"{row.year:04d}-{row.month:02d}" != self.period:
                raise ValueError("target falls outside snapshot period")

        incentive_keys = [row.external_person_id for row in self.incentives]
        if len(incentive_keys) != len(set(incentive_keys)):
            raise ValueError("duplicate incentive identity")
        for row in self.incentives:
            if row.external_person_id not in known_people:
                raise ValueError("incentive references an unknown person")
            if f"{row.year:04d}-{row.month:02d}" != self.period:
                raise ValueError("incentive falls outside snapshot period")

        payroll_keys = [(row.external_person_id, row.input_kind) for row in self.payroll_inputs]
        if len(payroll_keys) != len(set(payroll_keys)):
            raise ValueError("duplicate payroll input identity")
        for row in self.payroll_inputs:
            if row.external_person_id not in known_people:
                raise ValueError("payroll input references an unknown person")
            if f"{row.year:04d}-{row.month:02d}" != self.period:
                raise ValueError("payroll input falls outside snapshot period")

        return self


__all__ = [
    "RETAIL_GRILE_SCHEMA_V1",
    "RetailGenerationV1",
    "RetailIncentiveV1",
    "RetailManagerScopeV1",
    "RetailPayrollInputV1",
    "RetailPersonV1",
    "RetailSalesStoreDayV1",
    "RetailSnapshotV1",
    "RetailStoreV1",
    "RetailTargetV1",
]
