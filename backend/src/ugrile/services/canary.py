"""Google canary structural readback service (AC-12).

The fake adapter (S5a) writes the structural Grila+Pontaj projection into
``sheet_projection_runs.payload``. The canary readback is the only path
that asserts the projection matches the expected lattice without ever
touching a live Google Sheet.

The service is deterministic:

* The expected lattice is the union of ``SiteDayAssignment`` (WORKING) and
  ``PontajProjection`` rows for the projection's calendar revision,
  scoped to one store and the tenants.
* A readback parses the persisted JSON, extracts the actual pontaj row
  keys (``pontaj::<business_date>::<person>``) and the grila row keys
  (``grila::<business_date>::<person>``) — the ``store_id`` segment is
  implicit because the projection is store-scoped — and compares them
  against the expected lattice. The result is
  ``matched`` / ``missing_keys`` / ``unexpected_keys``.
* The result is recorded on the canary response so the close checklist
  ``SHEET_CANARY_REQUIRED`` blocker can be wired without the live canary
  authority (S5b only — the actual enforcement belongs to S6).

The service is read-only; no Google API is called. The fake adapter is
the sole writer of ``sheet_projection_runs.payload``; the canary readback
re-reads it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import DayStatus
from ..repositories.models import (
    PontajProjection,
    SheetProjectionRun,
    SiteDayAssignment,
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


def _actual_keys(payload: dict[str, object]) -> set[str]:
    """Extract the key set emitted by the fake adapter.

    The adapter payload shape (services/google.py:_build_payload) writes
    ``grila`` rows as ``{business_date, person_id, status, working_kind,
    revision}`` and ``pontaj`` rows as ``{person_id, business_date,
    status, start_time, end_time, pause_minutes, hours}`` — neither set
    carries ``month_id`` and the grila rows are store-scoped by the
    projection itself, so the ``store_id`` segment is implicit in the
    key. The canonical key contract is therefore:

    * ``pontaj::<business_date>::<person>``
    * ``grila::<business_date>::<person>``

    Anything else in the payload is ignored; the canary only asserts the
    structural lattice, not the per-row payload.
    """

    keys: set[str] = set()
    pontaj = payload.get("pontaj", {}) if isinstance(payload, dict) else {}
    if isinstance(pontaj, dict):
        rows = pontaj.get("rows", []) if isinstance(pontaj.get("rows"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            business_date = row.get("business_date") or ""
            person_id = row.get("person_id") or ""
            if business_date and person_id:
                keys.add(f"pontaj::{business_date}::{person_id}")
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


def _payload_revision(payload: dict[str, object]) -> int | None:
    """Return the calendar revision the projection was rendered at.

    Both ``grila`` and ``pontaj`` blocks carry the same ``revision`` value
    when produced by the canonical adapter; either is enough. ``None``
    means the payload is malformed (no revision fingerprint) and the
    caller should treat the canary as a no-op rather than fabricate a
    revision.
    """

    for block_key in ("grila", "pontaj"):
        block = payload.get(block_key) if isinstance(payload, dict) else None
        if isinstance(block, dict):
            revision = block.get("revision")
            if isinstance(revision, int):
                return revision
    return None


def _expected_keys(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    month_id: str,
    revision: int | None,
) -> set[str]:
    """Build the expected structural key set for the store.

    The expected set is the union of:

    * one ``grila::<business_date>::<person>`` per ``SiteDayAssignment``
      (WORKING) row for the store/month at the projection revision;
    * one ``pontaj::<business_date>::<person>`` per ``PontajProjection``
      row at the projection revision for the persons assigned to the
      store in the month.

    When the projection revision is unknown (legacy payload without a
    ``revision`` fingerprint), the PontajProjection leg is skipped
    because the projection cannot reliably mint PontajProjection keys
    without a revision; the grila leg still uses the bare
    SiteDayAssignment (which is single-revision per month in practice).
    """

    keys: set[str] = set()
    assignment_query = select(SiteDayAssignment).where(
        SiteDayAssignment.tenant_id == tenant_id,
        SiteDayAssignment.month_id == month_id,
        SiteDayAssignment.store_id == store_id,
        SiteDayAssignment.status == DayStatus.WORKING.value,
    )
    if revision is not None:
        assignment_query = assignment_query.where(SiteDayAssignment.revision == revision)
    assignments = list(session.execute(assignment_query).scalars())
    assigned_person_ids = {a.person_id for a in assignments}
    for a in assignments:
        keys.add(f"grila::{a.business_date.isoformat()}::{a.person_id}")
    if assigned_person_ids and revision is not None:
        pontaj_rows = list(
            session.execute(
                select(PontajProjection).where(
                    PontajProjection.tenant_id == tenant_id,
                    PontajProjection.month_id == month_id,
                    PontajProjection.revision == revision,
                    PontajProjection.person_id.in_(assigned_person_ids),
                )
            ).scalars()
        )
        for p in pontaj_rows:
            keys.add(f"pontaj::{p.business_date.isoformat()}::{p.person_id}")
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
    run = (
        session.execute(
            select(SheetProjectionRun)
            .where(
                SheetProjectionRun.tenant_id == tenant_id,
                SheetProjectionRun.store_id == store_id,
            )
            .order_by(SheetProjectionRun.last_run_at.desc().nullslast())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if run is None:
        return CanaryResult(
            store_id=store_id,
            matched=0,
            missing_keys=[],
            unexpected_keys=[],
            generation=None,
            last_run_at=None,
            last_error="NO_PROJECTION",
        )
    payload = json.loads(run.payload or "{}")
    actual = _actual_keys(payload)
    projection_revision = _payload_revision(payload)
    expected = _expected_keys(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        month_id=month_id,
        revision=projection_revision,
    )
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
