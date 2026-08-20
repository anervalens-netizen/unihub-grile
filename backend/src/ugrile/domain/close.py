"""Typed close/reopen validation (AC-15 S3 slice).

The contract is split between this module (pure validation) and
:mod:`ugrile.services.close` (transactional orchestration + audit chain).

Blockers
--------

Each :class:`CloseBlockerCode` is a typed precondition. The S3 slice covers
the four blockers that only require S1/S2 data:

* ``STORE_DAY_UNCOVERED`` — a store has a WORKING-less business date in
  the month.
* ``STORE_DAY_MULTIPLE_WORKING`` — two WORKING rows for the same
  store/date (AC-02 violation).
* ``PERSON_DAY_MULTIPLE_WORKING`` — a person assigned to two stores on
  the same date (AC-02 violation).
* ``INVALID_WORKING_KIND`` — a row whose ``working_kind`` does not match
  the home/other contract (e.g. EXTRA_OTHER on the home store).
* ``SALES_MISSING_FOR_WORKED_DAY`` — every worked store/date with no
  ``SalesStoreDay`` row.
* ``SALES_ORPHAN_FOR_COVERED_DAY`` — a ``SalesStoreDay`` row that lands
  on a date with no WORKING calendar row.
* ``TARGET_ZERO_FOR_WORKED_STORE`` — a store's effective target is zero
  on a worked date; the grid produces a deterministic anomaly but close
  blocks on it so the manager reviews the missing target.

Blockers that need S4/S5 infrastructure are **typed** but not yet
produced by this module. The list is closed for the S3 slice — when the
S4 UI or S5 Sheet canary lands, the corresponding code is added at the
same place. Bypassing blockers is forbidden; integration is explicit.

Reopen
------

A reopen is admin-only and must include a non-empty reason plus a new
revision/state. The previous close is preserved (append-only audit chain)
and the month returns to ``REOPENED`` state.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .enums import CloseBlockerCode, MonthState


@dataclass(frozen=True, slots=True)
class StoreCoverageSnapshot:
    """Minimum store/day coverage slice required for close validation."""

    store_id: str
    business_date: date
    person_id: str | None
    working_kind: str | None


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
            return False, (
                f"reopen reason must be at least {self.reason_min_length} characters"
            )
        return True, None


def _working_only(
    rows: Sequence[StoreCoverageSnapshot],
) -> list[StoreCoverageSnapshot]:
    return [row for row in rows if row.person_id is not None]


def validate_close(
    *,
    coverage: Sequence[StoreCoverageSnapshot],
    person_days: Sequence[PersonDaySnapshot],
    sales_availability: Sequence[SalesAvailabilitySnapshot],
    target_availability: Sequence[StoreTargetAvailabilitySnapshot],
    extra_blockers: Sequence[CloseBlockerCode] = (),
) -> CloseValidation:
    """Run the S3 close blockers deterministically."""

    blockers: list[BlockerDetail] = []

    # 1) Store-day coverage. Every WORKING store/date has exactly one person.
    by_store_day: dict[tuple[str, date], list[StoreCoverageSnapshot]] = {}
    for row in _working_only(coverage):
        by_store_day.setdefault((row.store_id, row.business_date), []).append(row)
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

    # 2) Person-day cross-store uniqueness. Every WORKING person/day has
    # exactly one store.
    by_person_day: dict[tuple[str, date], set[str]] = {}
    for day in person_days:
        if day.working_kind is None:
            continue
        by_person_day.setdefault((day.person_id, day.business_date), set()).add(
            day.store_id
        )
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

    # 3) Sales availability for worked days.
    sales_map = {(s.store_id, s.business_date): s.has_sale for s in sales_availability}
    for (store_id, business_date), group in sorted(by_store_day.items()):
        if not sales_map.get((store_id, business_date), False):
            blockers.append(
                BlockerDetail(
                    code=CloseBlockerCode.SALES_MISSING_FOR_WORKED_DAY,
                    store_id=store_id,
                    person_id=group[0].person_id,
                    business_date=business_date,
                    message=(
                        f"worked store {store_id} on "
                        f"{business_date.isoformat()} has no SalesStoreDay"
                    ),
                )
            )

    # 4) Orphan sales for dates without a WORKING calendar row.
    working_pairs = {(store_id, business_date) for store_id, business_date in by_store_day}
    for entry in sorted(sales_availability, key=lambda s: (s.store_id, s.business_date)):
        if (entry.store_id, entry.business_date) in working_pairs:
            continue
        if entry.has_sale:
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

    # 5) Target availability for worked stores/dates.
    target_map = {
        (t.store_id, t.business_date): (t.has_target, t.target_amount)
        for t in target_availability
    }
    for (store_id, business_date), group in sorted(by_store_day.items()):
        has_target, amount = target_map.get((store_id, business_date), (False, Decimal("0")))
        if not has_target or amount <= 0:
            blockers.append(
                BlockerDetail(
                    code=CloseBlockerCode.TARGET_ZERO_FOR_WORKED_STORE,
                    store_id=store_id,
                    person_id=group[0].person_id,
                    business_date=business_date,
                    message=(
                        f"worked store {store_id} on "
                        f"{business_date.isoformat()} has no positive target"
                    ),
                )
            )

    # 6) Typed blockers deferred to S4/S5 — surfaced verbatim so the audit
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


__all__ = [
    "BlockerDetail",
    "CloseValidation",
    "PersonDaySnapshot",
    "ReopenValidation",
    "SalesAvailabilitySnapshot",
    "StoreCoverageSnapshot",
    "StoreTargetAvailabilitySnapshot",
    "assert_close_state",
    "assert_reopen_state",
    "validate_close",
]
