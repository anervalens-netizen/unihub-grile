"""Typed close/reopen validation (AC-15 S3 slice).

The contract is split between this module (pure validation) and
:mod:`ugrile.services.close` (transactional orchestration + audit chain).

Open-store/day lattice
-----------------------

The S3 authority for "open" is **every active store for every date of the
month** — there is no separate closed-day table. The service builds the
authoritative lattice (:class:`OpenStoreDay`) and passes it to
:func:`validate_close`; a missing assignment row for an open day is an
explicit ``STORE_DAY_UNCOVERED`` blocker, never a silent "store closed".
OFF/LEAVE rows do not occupy store coverage and never satisfy a lattice
day.

Blockers
--------

Each :class:`CloseBlockerCode` is a typed precondition evaluated over the
full open-store/day lattice:

* ``STORE_DAY_UNCOVERED`` — an open store/day with no WORKING assignment
  row (a missing row, or only OFF/LEAVE rows).
* ``STORE_DAY_MULTIPLE_WORKING`` — two WORKING rows for the same
  store/date (AC-02 violation).
* ``PERSON_DAY_MULTIPLE_WORKING`` — a person assigned to two stores on
  the same date (AC-02 violation).
* ``INVALID_WORKING_KIND`` — a WORKING row whose ``working_kind`` does
  not match the home/other contract (e.g. EXTRA_OTHER on the home store).
* ``SALES_MISSING_FOR_WORKED_DAY`` — an open store/date with no
  ``SalesStoreDay`` row (full-lattice check).
* ``SALES_ORPHAN_FOR_COVERED_DAY`` — a ``SalesStoreDay`` row that lands
  on a date with no WORKING calendar row and therefore cannot be
  attributed.
* ``TARGET_ZERO_FOR_WORKED_STORE`` — an open store/date whose effective
  target is missing or zero (full-lattice check); the grid produces a
  deterministic anomaly but close blocks on it so the manager reviews the
  missing target.

Blockers that need S4/S5 infrastructure are **typed** but not yet
produced by this module. The list is closed for the S3 slice — when the
S4 UI or S5 Sheet canary lands, the corresponding code is added at the
same place. Bypassing blockers is forbidden; integration is explicit.

Reopen
------

A reopen is admin-only and must include a non-empty reason plus a new
revision/state. The previous close is preserved (append-only audit chain)
and the month returns to ``REOPENED`` state.

Audit chain proof
-----------------

Every close/reopen event carries a deterministic SHA-256 digest over its
own persisted fields plus the digest of the previous event in the month's
chain (:func:`month_close_event_digest`). ``verify_month_close_chain``
recomputes the chain and reports any broken link, so an append-only chain
is also provably intact.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .enums import CloseBlockerCode, MonthState, WorkingKind


@dataclass(frozen=True, slots=True)
class OpenStoreDay:
    """One authoritative open store/day of the month lattice (AC-15).

    The S3 authority for "open" is every active store for every date of the
    month — there is no separate closed-day table. A missing assignment row
    for an ``OpenStoreDay`` is a blocker (``STORE_DAY_UNCOVERED``), never a
    silent "store closed". OFF/LEAVE rows do not occupy store coverage.
    """

    store_id: str
    business_date: date


@dataclass(frozen=True, slots=True)
class StoreCoverageSnapshot:
    """Minimum store/day coverage slice required for close validation.

    ``person_home_store_id`` is populated by the service from the person
    master so the validator can enforce the NORMAL/EXTRA_HOME/EXTRA_OTHER
    home/other contract (``INVALID_WORKING_KIND``).
    """

    store_id: str
    business_date: date
    person_id: str | None
    working_kind: str | None
    person_home_store_id: str | None = None


@dataclass(frozen=True, slots=True)
class PersonDaySnapshot:
    """Minimum person/day slice required for the cross-store uniqueness check."""

    person_id: str
    business_date: date
    store_id: str
    working_kind: str | None


@dataclass(frozen=True, slots=True)
class SalesAvailabilitySnapshot:
    """All worked store/date pairs and the available sales generations."""

    store_id: str
    business_date: date
    has_sale: bool


@dataclass(frozen=True, slots=True)
class StoreTargetAvailabilitySnapshot:
    """Effective per-store target availability for the worked dates."""

    store_id: str
    business_date: date
    has_target: bool
    target_amount: Decimal


@dataclass(frozen=True, slots=True)
class BlockerDetail:
    """A typed close blocker surfaced to the API.

    Each blocker carries enough context for the manager UI to render the
    blocking condition deterministically without re-running validation.
    """

    code: CloseBlockerCode
    store_id: str | None
    person_id: str | None
    business_date: date | None
    message: str


@dataclass(frozen=True, slots=True)
class CloseValidation:
    blockers: tuple[BlockerDetail, ...]

    @property
    def ok(self) -> bool:
        return not self.blockers


@dataclass(frozen=True, slots=True)
class ReopenValidation:
    reason_required: bool
    reason_min_length: int = 4

    def validate_reason(self, reason: str | None) -> tuple[bool, str | None]:
        if not reason or not reason.strip():
            return False, "reopen reason is required"
        if len(reason.strip()) < self.reason_min_length:
            return False, (f"reopen reason must be at least {self.reason_min_length} characters")
        return True, None


def _working_only(
    rows: Sequence[StoreCoverageSnapshot],
) -> list[StoreCoverageSnapshot]:
    return [row for row in rows if row.person_id is not None]


def validate_close(
    *,
    open_days: Sequence[OpenStoreDay],
    coverage: Sequence[StoreCoverageSnapshot],
    person_days: Sequence[PersonDaySnapshot],
    sales_availability: Sequence[SalesAvailabilitySnapshot],
    target_availability: Sequence[StoreTargetAvailabilitySnapshot],
    extra_blockers: Sequence[CloseBlockerCode] = (),
) -> CloseValidation:
    """Run the S3 close blockers deterministically over the open lattice.

    ``open_days`` is the authoritative open-store/day lattice (every active
    store x every date of the month). Every blocker is evaluated over that
    lattice: a missing assignment row, a missing physical sale, or a
    missing/zero target for an open day is an explicit typed blocker —
    calculation completeness is never silently bypassed.
    """

    blockers: list[BlockerDetail] = []
    lattice = {(day.store_id, day.business_date) for day in open_days}
    working = _working_only(coverage)

    # 1) Store-day coverage over the full lattice: every open store/day must
    # have exactly one WORKING assignment row. A missing row (or only
    # OFF/LEAVE rows) is STORE_DAY_UNCOVERED.
    by_store_day: dict[tuple[str, date], list[StoreCoverageSnapshot]] = {}
    for row in working:
        by_store_day.setdefault((row.store_id, row.business_date), []).append(row)
    for store_id, business_date in sorted(lattice):
        group = by_store_day.get((store_id, business_date), [])
        if not group:
            blockers.append(
                BlockerDetail(
                    code=CloseBlockerCode.STORE_DAY_UNCOVERED,
                    store_id=store_id,
                    person_id=None,
                    business_date=business_date,
                    message=(
                        f"active store {store_id} on {business_date.isoformat()} "
                        "has no working assignment"
                    ),
                )
            )

    # 2) Multiple working agents per store/day (AC-02 violation).
    for (store_id, business_date), group in sorted(by_store_day.items()):
        if len(group) > 1:
            blockers.append(
                BlockerDetail(
                    code=CloseBlockerCode.STORE_DAY_MULTIPLE_WORKING,
                    store_id=store_id,
                    person_id=None,
                    business_date=business_date,
                    message=(
                        f"store {store_id} has {len(group)} working agents on "
                        f"{business_date.isoformat()}"
                    ),
                )
            )

    # 3) Person-day cross-store uniqueness. Every WORKING person/day has
    # exactly one store.
    by_person_day: dict[tuple[str, date], set[str]] = {}
    for day in person_days:
        if day.working_kind is None:
            continue
        by_person_day.setdefault((day.person_id, day.business_date), set()).add(day.store_id)
    for (person_id, business_date), stores in sorted(by_person_day.items()):
        if len(stores) > 1:
            blockers.append(
                BlockerDetail(
                    code=CloseBlockerCode.PERSON_DAY_MULTIPLE_WORKING,
                    store_id=None,
                    person_id=person_id,
                    business_date=business_date,
                    message=(
                        f"person {person_id} works in {len(stores)} stores on "
                        f"{business_date.isoformat()}"
                    ),
                )
            )

    # 4) Invalid working classification: a WORKING row must respect the
    # home/other contract (NORMAL/EXTRA_HOME on the home store, EXTRA_OTHER
    # on a different store) and must carry a working kind.
    for row in sorted(working, key=lambda r: (r.store_id, r.business_date, r.person_id or "")):
        kind = row.working_kind
        home = row.person_home_store_id
        invalid = kind is None
        if not invalid and home is not None:
            if kind in {WorkingKind.NORMAL.value, WorkingKind.EXTRA_HOME.value}:
                invalid = home != row.store_id
            elif kind == WorkingKind.EXTRA_OTHER.value:
                invalid = home == row.store_id
        if invalid:
            blockers.append(
                BlockerDetail(
                    code=CloseBlockerCode.INVALID_WORKING_KIND,
                    store_id=row.store_id,
                    person_id=row.person_id,
                    business_date=row.business_date,
                    message=(
                        f"working kind {kind or 'NONE'} for person {row.person_id} "
                        f"at store {row.store_id} on {row.business_date.isoformat()} "
                        "violates the home/other contract"
                    ),
                )
            )

    # 5) Sales availability over the full lattice: every open store/day must
    # have a SalesStoreDay row. A sale row on an open day with no WORKING
    # calendar row cannot be attributed (orphan).
    sales_map = {(s.store_id, s.business_date): s.has_sale for s in sales_availability}
    for store_id, business_date in sorted(lattice):
        group = by_store_day.get((store_id, business_date)) or []
        if not sales_map.get((store_id, business_date), False):
            blockers.append(
                BlockerDetail(
                    code=CloseBlockerCode.SALES_MISSING_FOR_WORKED_DAY,
                    store_id=store_id,
                    person_id=group[0].person_id if group else None,
                    business_date=business_date,
                    message=(
                        f"open store {store_id} on "
                        f"{business_date.isoformat()} has no SalesStoreDay"
                    ),
                )
            )
    working_pairs = set(by_store_day)
    for entry in sorted(sales_availability, key=lambda s: (s.store_id, s.business_date)):
        if not entry.has_sale:
            continue
        if (entry.store_id, entry.business_date) in working_pairs:
            continue
        blockers.append(
            BlockerDetail(
                code=CloseBlockerCode.SALES_ORPHAN_FOR_COVERED_DAY,
                store_id=entry.store_id,
                person_id=None,
                business_date=entry.business_date,
                message=(
                    f"SalesStoreDay for store {entry.store_id} on "
                    f"{entry.business_date.isoformat()} has no working calendar row"
                ),
            )
        )

    # 6) Target availability over the full lattice: every open store/day must
    # have a positive effective target.
    target_map = {
        (t.store_id, t.business_date): (t.has_target, t.target_amount) for t in target_availability
    }
    for store_id, business_date in sorted(lattice):
        group = by_store_day.get((store_id, business_date)) or []
        has_target, amount = target_map.get((store_id, business_date), (False, Decimal("0")))
        if not has_target or amount <= 0:
            blockers.append(
                BlockerDetail(
                    code=CloseBlockerCode.TARGET_ZERO_FOR_WORKED_STORE,
                    store_id=store_id,
                    person_id=group[0].person_id if group else None,
                    business_date=business_date,
                    message=(
                        f"open store {store_id} on "
                        f"{business_date.isoformat()} has no positive target"
                    ),
                )
            )

    # 7) Typed blockers deferred to S4/S5 — surfaced verbatim so the audit
    # chain records them as pending. They do not fail close by themselves
    # until the corresponding integration lands.
    for code in extra_blockers:
        blockers.append(
            BlockerDetail(
                code=code,
                store_id=None,
                person_id=None,
                business_date=None,
                message=f"deferred blocker: {code.value}",
            )
        )

    blockers.sort(
        key=lambda b: (
            b.code.value,
            b.store_id or "",
            b.person_id or "",
            b.business_date or date.min,
        )
    )
    return CloseValidation(blockers=tuple(blockers))


def assert_close_state(state: str | MonthState) -> None:
    """A month must be in ``OPEN`` to be closed; only ``CLOSED`` can reopen."""

    from .errors import MonthStateError

    value = state.value if isinstance(state, MonthState) else state
    if value not in {MonthState.OPEN.value, MonthState.REOPENED.value}:
        raise MonthStateError(
            "month is not in a closeable state",
            details={
                "code": "MONTH_NOT_OPEN",
                "state": value,
                "allowed": [MonthState.OPEN.value, MonthState.REOPENED.value],
            },
        )


def assert_reopen_state(state: str | MonthState) -> None:
    from .errors import MonthStateError

    value = state.value if isinstance(state, MonthState) else state
    if value != MonthState.CLOSED.value:
        raise MonthStateError(
            "month is not closed; reopen is not applicable",
            details={
                "code": "MONTH_NOT_CLOSED",
                "state": value,
                "expected": MonthState.CLOSED.value,
            },
        )


def month_close_event_digest(
    *,
    tenant_id: str,
    month_id: str,
    action: str,
    previous_state: str,
    new_state: str,
    revision_before: int,
    revision_after: int,
    actor_id: str,
    reason: str | None,
    blockers: str,
    previous_event_digest: str | None,
) -> str:
    """Deterministic SHA-256 digest of one close/reopen audit event.

    The digest covers the persisted deterministic fields plus the digest of
    the previous event in the month's chain (``None`` for the first event).
    ``occurred_at`` and the row id are excluded so the digest is fully
    reproducible. ``blockers`` is the canonical JSON string stored on the
    row. Alembic migration 0007 backfills legacy rows with the same scheme.
    """

    payload = json.dumps(
        {
            "tenant_id": tenant_id,
            "month_id": month_id,
            "action": action,
            "previous_state": previous_state,
            "new_state": new_state,
            "revision_before": revision_before,
            "revision_after": revision_after,
            "actor_id": actor_id,
            "reason": reason,
            "blockers": blockers,
            "previous_event_digest": previous_event_digest,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MonthCloseEventRecord:
    """Persisted close-event fields needed for chain verification."""

    id: int
    tenant_id: str
    month_id: str
    action: str
    previous_state: str
    new_state: str
    revision_before: int
    revision_after: int
    actor_id: str
    reason: str | None
    blockers: str
    previous_event_digest: str | None
    event_digest: str


def verify_month_close_chain(records: Sequence[MonthCloseEventRecord]) -> list[str]:
    """Return deterministic integrity issues for an ordered close-event chain.

    An empty list means the chain is intact. Each row's own digest must
    match the deterministic recomputation over its fields chained to the
    previous row's digest, and ``previous_event_digest`` must equal the
    previous row's ``event_digest``.
    """

    issues: list[str] = []
    previous: str | None = None
    for record in records:
        expected = month_close_event_digest(
            tenant_id=record.tenant_id,
            month_id=record.month_id,
            action=record.action,
            previous_state=record.previous_state,
            new_state=record.new_state,
            revision_before=record.revision_before,
            revision_after=record.revision_after,
            actor_id=record.actor_id,
            reason=record.reason,
            blockers=record.blockers,
            previous_event_digest=previous,
        )
        if record.event_digest != expected:
            issues.append(f"event {record.id}: event_digest mismatch")
        if record.previous_event_digest != previous:
            issues.append(f"event {record.id}: previous_event_digest mismatch")
        previous = record.event_digest
    return issues


__all__ = [
    "BlockerDetail",
    "CloseValidation",
    "MonthCloseEventRecord",
    "OpenStoreDay",
    "PersonDaySnapshot",
    "ReopenValidation",
    "SalesAvailabilitySnapshot",
    "StoreCoverageSnapshot",
    "StoreTargetAvailabilitySnapshot",
    "assert_close_state",
    "assert_reopen_state",
    "month_close_event_digest",
    "validate_close",
    "verify_month_close_chain",
]
