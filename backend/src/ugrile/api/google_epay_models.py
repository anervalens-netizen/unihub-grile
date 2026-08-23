"""Typed API response for Google-backed E-pay readback."""

from __future__ import annotations

from pydantic import BaseModel

from .schemas import EpayReadbackItemOut


class GoogleEpayReadbackOut(BaseModel):
    store_id: str
    month_id: str
    observed_at: str
    valid_count: int
    invalid_count: int
    structure_valid: bool
    structural_errors: list[str]
    items: list[EpayReadbackItemOut]


__all__ = ["GoogleEpayReadbackOut"]
