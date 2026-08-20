"""Versioned connector types (v1) and a fixture connector.

The connector is the only sanctioned way for the foundation to receive
external data. Retail imports are explicitly forbidden at S1 — Stage 7 owns
the versioned Retail contract; until then only fixtures are accepted.

The v1 contract is intentionally narrow:

* ``stores`` — required internal/external codes, company, name, active flag.
* ``people`` — internal/external codes, home store, display name, active.
* ``sales`` — per (store, business_date) total in the fixture generation.
* ``targets`` — per (store, year, month, kind) target with an explicit
  ``version``. The version makes the target a first-class versioned input:
  later stages can supersede a target without losing history and reconcile
  against the connector generation.

Validation is structural; semantic validation lives in the domain layer
(``ugrile.domain.calendar``). The connector never imports from
``/opt/Mobiup/unihub-retail``; the import-boundary test enforces this at the
filesystem level.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..domain.enums import ConnectorGeneration


class ConnectorHeader(BaseModel):
    """First record of a v1 payload, describing the schema version."""

    schema_name: Literal["grile.connector.v1"] = Field(alias="schema")
    generation: str
    tenant_id: str
    emitted_at: str

    @field_validator("generation")
    @classmethod
    def _validate_generation(cls, value: str) -> str:
        if value != ConnectorGeneration.FIXTURE_V1:
            raise ValueError(f"unsupported generation: {value}")
        return value


class StoreRecord(BaseModel):
    tenant_id: str
    internal_code: str
    external_code: str | None = None
    company_code: str
    name: str
    is_active: bool = True


class PersonRecord(BaseModel):
    tenant_id: str
    internal_code: str
    external_code: str | None = None
    home_store_internal_code: str
    display_name: str
    is_active: bool = True


class SalesRecord(BaseModel):
    tenant_id: str
    store_internal_code: str
    business_date: date
    amount: Decimal
    currency: str = "RON"
    source_ref: str | None = None

    @field_validator("amount")
    @classmethod
    def _validate_amount(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("amount must be non-negative")
        return value


# Allowed target kinds. Must match the DB check constraint
# ``store_target_kind_enum`` so a fixture never carries a kind the engine
# cannot store.
TARGET_KINDS = ("MONTHLY_SALES", "MONTHLY_UNITS", "MONTHLY_ATTACH")


class TargetRecord(BaseModel):
    """Versioned per-store/month target.

    ``version`` starts at ``1`` and increments on supersede. The composite
    ``(tenant_id, store_id, year, month, kind, version)`` is unique, so
    re-applying the same payload is idempotent and superseding is auditable.
    """

    tenant_id: str
    store_internal_code: str
    year: int
    month: int
    kind: Literal["MONTHLY_SALES", "MONTHLY_UNITS", "MONTHLY_ATTACH"]
    version: int = 1
    amount: Decimal
    currency: str = "RON"

    @field_validator("month")
    @classmethod
    def _validate_month(cls, value: int) -> int:
        if not 1 <= value <= 12:
            raise ValueError(f"month out of range: {value}")
        return value

    @field_validator("amount")
    @classmethod
    def _validate_amount(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("target amount must be non-negative")
        return value

    @model_validator(mode="after")
    def _validate_version(self) -> TargetRecord:
        if self.version < 1:
            raise ValueError("target version must be >= 1")
        return self


class ConnectorV1Payload(BaseModel):
    """Top-level payload. Validation order is structural only."""

    header: ConnectorHeader
    stores: list[StoreRecord] = Field(default_factory=list)
    people: list[PersonRecord] = Field(default_factory=list)
    sales: list[SalesRecord] = Field(default_factory=list)
    targets: list[TargetRecord] = Field(default_factory=list)


__all__ = [
    "ConnectorHeader",
    "ConnectorV1Payload",
    "PersonRecord",
    "SalesRecord",
    "StoreRecord",
    "TARGET_KINDS",
    "TargetRecord",
]
