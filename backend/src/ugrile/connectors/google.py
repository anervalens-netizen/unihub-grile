"""Fake Google Sheets adapter (AC-12 S5a slice).

The contract is explicit:

* The fake adapter never mutates Google Sheets; it persists structural
  projections locally so tests/dev can exercise the same provider seam.
* A missing local binding may be bootstrapped to the deterministic fake Sheet
  identity, but an existing binding is never re-pointed as a projection side
  effect. Projection may only advance its generation.
* It exposes ``write_store_projection(store_id, generation, payload)`` and
  ``read_store_projection(store_id)`` so the manager UI / canary readback can
  verify the structural shape of a Grila + Pontaj projection without touching
  any Google API.
* Successful fake projections persist the same format/checksum reconciliation
  envelope that the live provider exposes after remote readback verification.
* Month-scoped readback trusts only v2 snapshots carrying an exact persisted
  ``metadata.month_id``; historical unscoped rows cannot masquerade as another
  month's current projection.
* Provider failure is simulated via ``UGR_S5_GOOGLE_FAIL=1`` while last-good
  projection state remains readable.

The adapter only depends on the local DB; no network I/O happens.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..domain.errors import DomainError
from ..repositories.models import SheetBinding, SheetProjectionRun
from .google_projection_format import reconciliation_metadata


class GoogleAdapterError(DomainError):
    """Retryable provider failure raised by the fake Google adapter."""


@dataclass(frozen=True, slots=True)
class StoreProjection:
    """A deterministic structural projection for one store."""

    store_id: str
    generation: str
    grila: Mapping[str, Any]
    pontaj: Mapping[str, Any]
    last_success_generation: str | None
    reconciliation: Mapping[str, Any] = field(default_factory=dict)


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def fake_spreadsheet_id(tenant_id: str, store_id: str) -> str:
    """Return the stable no-network Sheet identity used by the fake provider."""

    return f"fake-sheet-{tenant_id}-{store_id}"


def is_provider_failing() -> bool:
    """Return ``True`` when the fake provider is configured to fail."""

    value = os.environ.get("UGR_S5_GOOGLE_FAIL", "")
    return value.strip() in {"1", "true", "TRUE", "yes", "on"}


def _run_matches_month(row: SheetProjectionRun, month_id: str) -> bool:
    payload = _json_loads(row.payload)
    metadata = payload.get("metadata")
    return isinstance(metadata, Mapping) and metadata.get("month_id") == month_id


def _last_run_for(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    month_id: str | None = None,
) -> SheetProjectionRun | None:
    rows = (
        session.query(SheetProjectionRun)
        .filter_by(tenant_id=tenant_id, store_id=store_id)
        .order_by(SheetProjectionRun.id.desc())
    )
    if month_id is None:
        return rows.first()
    for row in rows:
        if _run_matches_month(row, month_id):
            return row
    return None


def _last_successful_run_for(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    month_id: str | None = None,
) -> SheetProjectionRun | None:
    """Return the newest successful projection, optionally scoped to one month."""

    rows = (
        session.query(SheetProjectionRun)
        .filter_by(tenant_id=tenant_id, store_id=store_id, status="DONE")
        .filter(SheetProjectionRun.last_success_generation.isnot(None))
        .order_by(SheetProjectionRun.id.desc())
    )
    if month_id is None:
        return rows.first()
    for row in rows:
        if _run_matches_month(row, month_id):
            return row
    return None


def _binding_for(session: Session, *, tenant_id: str, store_id: str) -> SheetBinding | None:
    return (
        session.query(SheetBinding).filter_by(tenant_id=tenant_id, store_id=store_id).one_or_none()
    )


def _ensure_binding(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    generation: str,
    spreadsheet_id: str,
) -> SheetBinding:
    """Create a missing fake binding or advance an existing binding generation.

    Existing Sheet identity is immutable here. Explicit rebind belongs only to
    the administrative Sheet-binding lifecycle service.
    """

    binding = _binding_for(session, tenant_id=tenant_id, store_id=store_id)
    now = _now_utc()
    if binding is None:
        binding = SheetBinding(
            tenant_id=tenant_id,
            store_id=store_id,
            spreadsheet_id=spreadsheet_id,
            sheet_name_grila="Grila",
            sheet_name_pontaj="Pontaj",
            generation=generation,
        )
        session.add(binding)
        return binding

    if binding.spreadsheet_id != spreadsheet_id:
        raise DomainError(
            "projection cannot change an existing Sheet binding",
            details={
                "code": "SHEET_BINDING_STALE",
                "store_id": store_id,
            },
        )
    binding.sheet_name_grila = binding.sheet_name_grila or "Grila"
    binding.sheet_name_pontaj = binding.sheet_name_pontaj or "Pontaj"
    binding.generation = generation
    binding.updated_at = now
    return binding


def write_store_projection(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    generation: str,
    payload: Mapping[str, Any],
    spreadsheet_id: str | None = None,
) -> StoreProjection:
    """Persist the structural projection for one store without network I/O."""

    if not store_id:
        raise GoogleAdapterError(
            "store_id is required",
            details={"tenant_id": tenant_id},
        )
    if not generation:
        raise GoogleAdapterError(
            "generation is required",
            details={"tenant_id": tenant_id, "store_id": store_id},
        )
    if not isinstance(payload, Mapping):
        raise GoogleAdapterError(
            "payload must be a mapping",
            details={"tenant_id": tenant_id, "store_id": store_id},
        )

    grila = payload.get("grila", {})
    pontaj = payload.get("pontaj", {})
    if not isinstance(grila, Mapping) or not isinstance(pontaj, Mapping):
        raise GoogleAdapterError(
            "grila and pontaj must be mappings",
            details={"tenant_id": tenant_id, "store_id": store_id},
        )

    existing_binding = _binding_for(session, tenant_id=tenant_id, store_id=store_id)
    resolved_spreadsheet_id = spreadsheet_id or (
        existing_binding.spreadsheet_id
        if existing_binding is not None
        else fake_spreadsheet_id(tenant_id, store_id)
    )
    if (
        existing_binding is not None
        and existing_binding.spreadsheet_id != resolved_spreadsheet_id
    ):
        raise DomainError(
            "projection cannot change an existing Sheet binding",
            details={"code": "SHEET_BINDING_STALE", "store_id": store_id},
        )

    metadata = payload.get("metadata")
    month_id = (
        str(metadata.get("month_id"))
        if isinstance(metadata, Mapping) and metadata.get("month_id")
        else None
    )
    if is_provider_failing():
        last_good = _last_successful_run_for(
            session,
            tenant_id=tenant_id,
            store_id=store_id,
            month_id=month_id,
        )
        last_success = last_good.last_success_generation if last_good is not None else None
        now = _now_utc()
        session.add(
            SheetProjectionRun(
                tenant_id=tenant_id,
                store_id=store_id,
                status="FAILED",
                last_error=(
                    "fake provider is failing (UGR_S5_GOOGLE_FAIL=1); "
                    "last-good projection retained"
                    if last_success
                    else "fake provider is failing (UGR_S5_GOOGLE_FAIL=1); "
                    "no prior good projection to retain"
                ),
                last_success_generation=last_success,
                last_run_at=now,
                payload=_json_dumps(payload),
                generation=generation,
                failures=1,
            )
        )
        raise GoogleAdapterError(
            "fake google provider is failing (UGR_S5_GOOGLE_FAIL=1)",
            details={
                "tenant_id": tenant_id,
                "store_id": store_id,
                "generation": generation,
                "last_success_generation": last_success,
            },
        )

    _ensure_binding(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        generation=generation,
        spreadsheet_id=resolved_spreadsheet_id,
    )
    reconciliation = reconciliation_metadata(
        payload,
        generation=generation,
        verification_mode="fake_local",
        verified=True,
    )
    persisted_payload = {
        "metadata": dict(metadata) if isinstance(metadata, Mapping) else {},
        "grila": dict(grila),
        "pontaj": dict(pontaj),
        "reconciliation": reconciliation,
    }
    now = _now_utc()
    run = SheetProjectionRun(
        tenant_id=tenant_id,
        store_id=store_id,
        status="DONE",
        last_error=None,
        last_success_generation=generation,
        last_run_at=now,
        payload=_json_dumps(persisted_payload),
        generation=generation,
        failures=0,
    )
    session.add(run)
    return StoreProjection(
        store_id=store_id,
        generation=generation,
        grila=dict(grila),
        pontaj=dict(pontaj),
        last_success_generation=generation,
        reconciliation=reconciliation,
    )


def read_store_projection(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    month_id: str | None = None,
) -> StoreProjection | None:
    """Return the last-good projection, optionally proven for one month."""

    row = _last_successful_run_for(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        month_id=month_id,
    )
    if row is None or row.last_success_generation is None:
        return None
    payload = _json_loads(row.payload)
    reconciliation = payload.get("reconciliation")
    return StoreProjection(
        store_id=store_id,
        generation=row.last_success_generation,
        grila=payload.get("grila", {}),
        pontaj=payload.get("pontaj", {}),
        last_success_generation=row.last_success_generation,
        reconciliation=(
            dict(reconciliation) if isinstance(reconciliation, Mapping) else {}
        ),
    )


def last_error(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    month_id: str | None = None,
) -> str | None:
    """Return the most recent adapter error string, optionally for one month."""

    row = _last_run_for(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        month_id=month_id,
    )
    return row.last_error if row is not None else None


def last_run_at(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    month_id: str | None = None,
) -> datetime | None:
    """Return the most recent projection run timestamp, optionally for one month."""

    row = _last_run_for(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        month_id=month_id,
    )
    return row.last_run_at if row is not None else None


def binding_for(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
) -> SheetBinding | None:
    """Return the canonical ``sheet_bindings`` row for the store."""

    return _binding_for(session, tenant_id=tenant_id, store_id=store_id)


__all__ = [
    "GoogleAdapterError",
    "StoreProjection",
    "binding_for",
    "fake_spreadsheet_id",
    "is_provider_failing",
    "last_error",
    "last_run_at",
    "read_store_projection",
    "write_store_projection",
]
