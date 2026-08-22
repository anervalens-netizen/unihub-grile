"""Fake Google Sheets adapter (AC-12 S5a slice).

The contract is explicit:

* The fake adapter is the **only** projection writer during S5a. It must
  not mutate Google Sheets. The real copied canary belongs to S5b once
  the Google canary authority is granted.
* It exposes ``write_store_projection(store_id, generation, payload)``
  and ``read_store_projection(store_id)`` so the manager UI / canary
  readback can verify the structural shape of a Grila + Pontaj
  projection without touching any Google API.
* The adapter persists projections in the local DB
  (``sheet_projection_runs`` / ``sheet_bindings``) and records
  ``last_error`` / ``last_success_generation`` / ``last_run_at`` so the
  last-good projection can survive a provider failure.
* Provider failure is simulated via the environment flag
  ``UGR_S5_GOOGLE_FAIL=1``. When set, every write raises
  ``GoogleAdapterError`` and the caller falls back to the last-good
  payload. When unset, writes succeed deterministically.

The adapter only depends on the local DB; no network I/O happens.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..domain.errors import DomainError
from ..repositories.models import SheetBinding, SheetProjectionRun


class GoogleAdapterError(DomainError):
    """A provider failure raised by the fake Google adapter.

    The caller is responsible for keeping the last-good projection alive
    in the DB when this exception is raised; the adapter never mutates
    state on failure.
    """


@dataclass(frozen=True, slots=True)
class StoreProjection:
    """A deterministic structural projection for one store.

    ``grila`` and ``pontaj`` are versioned dicts the manager UI / canary
    readback can introspect. The adapter never writes these to a Google
    Sheet during S5a; it only persists them in
    ``sheet_projection_runs.payload`` so the structural readback can
    match the expected lattice.
    """

    store_id: str
    generation: str
    grila: Mapping[str, Any]
    pontaj: Mapping[str, Any]
    last_success_generation: str | None


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


def is_provider_failing() -> bool:
    """Return ``True`` when the fake provider is configured to fail.

    The flag is read from ``UGR_S5_GOOGLE_FAIL``. Production deployments
    keep the flag unset; tests flip it on to simulate outages.
    """

    value = os.environ.get("UGR_S5_GOOGLE_FAIL", "")
    return value.strip() in {"1", "true", "TRUE", "yes", "on"}


def _last_run_for(session: Session, *, tenant_id: str, store_id: str) -> SheetProjectionRun | None:
    return (
        session.query(SheetProjectionRun)
        .filter_by(tenant_id=tenant_id, store_id=store_id)
        .order_by(SheetProjectionRun.id.desc())
        .first()
    )


def _last_successful_run_for(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
) -> SheetProjectionRun | None:
    """Return the newest persisted successful projection, ignoring failures."""

    return (
        session.query(SheetProjectionRun)
        .filter_by(tenant_id=tenant_id, store_id=store_id, status="DONE")
        .filter(SheetProjectionRun.last_success_generation.isnot(None))
        .order_by(SheetProjectionRun.id.desc())
        .first()
    )


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
    """Upsert the canonical ``sheet_bindings`` row for the store."""

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
    else:
        binding.spreadsheet_id = spreadsheet_id
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
    """Persist the structural projection for one store.

    The adapter only writes the local DB rows (``sheet_projection_runs``
    + ``sheet_bindings``). When ``UGR_S5_GOOGLE_FAIL=1`` is set, the
    function raises :class:`GoogleAdapterError` and the caller is
    responsible for keeping the last-good payload alive.
    """

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

    if is_provider_failing():
        # Append a NEW FAILED row alongside prior DONE rows. The authoritative
        # last-good payload remains the newest successful row, even after
        # multiple consecutive provider failures.
        last_good = _last_successful_run_for(session, tenant_id=tenant_id, store_id=store_id)
        last_success = last_good.last_success_generation if last_good is not None else None
        now = _now_utc()
        session.add(
            SheetProjectionRun(
                tenant_id=tenant_id,
                store_id=store_id,
                status="FAILED",
                last_error=(
                    "fake provider is failing (UGR_S5_GOOGLE_FAIL=1); "
                    "last-good projection retained" if last_success else
                    "fake provider is failing (UGR_S5_GOOGLE_FAIL=1); "
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

    spreadsheet_id = spreadsheet_id or f"fake-sheet-{tenant_id}-{store_id}"
    _ensure_binding(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        generation=generation,
        spreadsheet_id=spreadsheet_id,
    )
    now = _now_utc()
    run = SheetProjectionRun(
        tenant_id=tenant_id,
        store_id=store_id,
        status="DONE",
        last_error=None,
        last_success_generation=generation,
        last_run_at=now,
        payload=_json_dumps(
            {
                "grila": dict(grila),
                "pontaj": dict(pontaj),
            }
        ),
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
    )


def read_store_projection(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
) -> StoreProjection | None:
    """Return the last-good projection for one store, or ``None``.

    Failed attempts are diagnostic rows only. Readback always returns the
    newest successful payload and therefore cannot expose an attempted-but-not-
    persisted provider projection as if it were last-good state.
    """

    row = _last_successful_run_for(session, tenant_id=tenant_id, store_id=store_id)
    if row is None or row.last_success_generation is None:
        return None
    payload = _json_loads(row.payload)
    return StoreProjection(
        store_id=store_id,
        generation=row.last_success_generation,
        grila=payload.get("grila", {}),
        pontaj=payload.get("pontaj", {}),
        last_success_generation=row.last_success_generation,
    )


def last_error(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
) -> str | None:
    """Return the most recent adapter error string for the store."""

    row = _last_run_for(session, tenant_id=tenant_id, store_id=store_id)
    return row.last_error if row is not None else None


def last_run_at(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
) -> datetime | None:
    """Return the ``last_run_at`` of the most recent projection run."""

    row = _last_run_for(session, tenant_id=tenant_id, store_id=store_id)
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
    "is_provider_failing",
    "last_error",
    "last_run_at",
    "read_store_projection",
    "write_store_projection",
]
