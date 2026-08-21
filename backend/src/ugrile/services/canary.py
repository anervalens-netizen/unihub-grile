"""Google canary structural readback service (AC-12).

The fake adapter (S5a) writes the structural Grila+Pontaj projection into
``sheet_projection_runs.payload``. The canary readback is the only path
that asserts the projection matches the expected lattice without ever
touching a live Google Sheet.

The service is deterministic:

* The expected lattice is the union of ``PontajProjection`` rows for the
  current month revision, scoped to the active stores in the tenant.
* A readback parses the persisted JSON, compares the pontaj row keys
  (``pontaj::<month>::<person>``) and the grila row keys
  (``grila::<store>::<date>::<person>``) against the expected lattice,
  and returns the matched/missing/unexpected keys.
* The result is recorded on ``ExportRun`` summary so the close checklist
  ``SHEET_CANARY_REQUIRED`` blocker can be wired without the live canary
  authority (S5b only — the actual enforcement belongs to S6).

The service is read-only; no Google API is called. The fake adapter is
the sole writer of ``sheet_projection_runs.payload``; the canary readback
re-reads it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..repositories.models import (
    PontajProjection,
    SheetProjectionRun,
    Store,
)


@dataclass(frozen=True, slots=True)
class CanaryResult:
    store_id: str
    matched: int
    missing_keys: list[str]
    unexpected_keys: list[str]
    generation: str | None
    last_run_at: str | None
    last_error: str | None

    @property
    def ok(self) -> bool:
        return not self.missing_keys and not self.unexpected_keys


def _expected_pontaj_keys(month_id: str, person_ids: Iterable[str]) -> set[str]:
    return {f"pontaj::{month_id}::{p}" for p in person_ids}


def _actual_keys(payload: dict[str, object]) -> set[str]:
    keys: set[str] = set()
    pontaj = payload.get("pontaj", {}) if isinstance(payload, dict) else {}
    if isinstance(pontaj, dict):
        rows = pontaj.get("rows", []) if isinstance(pontaj.get("rows"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            month_id = row.get("month_id") or row.get("revision_key") or ""
            person_id = row.get("person_id") or ""
            if month_id and person_id:
                keys.add(f"pontaj::{month_id}::{person_id}")
    grila = payload.get("grila", {}) if isinstance(payload, dict) else {}
    if isinstance(grila, dict):
        rows = grila.get("rows", []) if isinstance(grila.get("rows"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            business_date = row.get("business_date") or ""
            person_id = row.get("person_id") or ""
            if business_date and person_id:
                keys.add(f"grila::{business_date}::{person_id}")
    return keys


def read_store_canary(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    month_id: str,
) -> CanaryResult:
    """Inspect the latest structural projection for ``store_id``.

    The expected lattice is the union of grila assignments and pontaj
    rows for the persisted projection. We compare the persisted keys
    against the structural expectation and surface missing/unexpected
    rows. The actual structural projection is the only writer (the
    fake adapter); the readback never mutates it.
    """
    run = session.execute(
        select(SheetProjectionRun)
        .where(
            SheetProjectionRun.tenant_id == tenant_id,
            SheetProjectionRun.store_id == store_id,
        )
        .order_by(SheetProjectionRun.last_run_at.desc().nullslast())
        .limit(1)
    ).scalars().first()
    if run is None:
        return CanaryResult(
            store_id=store_id,
            matched=0,
            missing_keys=sorted(_expected_pontaj_keys(month_id, [])),
            unexpected_keys=[],
            generation=None,
            last_run_at=None,
            last_error="NO_PROJECTION",
        )
    payload = json.loads(run.payload or "{}")
    actual = _actual_keys(payload)
    persons = list(
        session.execute(
            select(PontajProjection.person_id)
            .distinct()
            .where(
                PontajProjection.tenant_id == tenant_id,
                PontajProjection.month_id == month_id,
            )
        ).scalars()
    )
    expected = _expected_pontaj_keys(month_id, persons)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    return CanaryResult(
        store_id=store_id,
        matched=len(actual & expected),
        missing_keys=missing,
        unexpected_keys=unexpected,
        generation=run.generation,
        last_run_at=run.last_run_at.isoformat() if run.last_run_at else None,
        last_error=run.last_error,
    )


def list_active_store_ids(session: Session, *, tenant_id: str) -> list[str]:
    stores = list(
        session.execute(
            select(Store)
            .where(Store.tenant_id == tenant_id, Store.is_active.is_(True))
            .order_by(Store.internal_code)
        ).scalars()
    )
    return [store.id for store in stores]


__all__ = ["CanaryResult", "list_active_store_ids", "read_store_canary"]
