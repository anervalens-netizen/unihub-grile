"""Pure helpers for calendar/coverage invariants (AC-02).

These helpers are deliberately side-effect-free. The repository and SQLAlchemy
layer re-apply the same checks at the DB transaction boundary via partial unique
indexes; the API surfaces the result as ``ConflictError`` (HTTP 409).

Two invariants are enforced:

* **Single working agent per store and business date.** A store may be closed
  (no entry) or have exactly one ``WORKING`` assignment per business date.
* **Single working site per agent and business date.** A person cannot work in
  two different stores on the same business date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .enums import DayStatus, WorkingKind


@dataclass(frozen=True, slots=True)
class SiteDayAssignment:
    """Minimal projection of a site-day assignment used by the domain helpers."""

    store_id: str
    person_id: str
    business_date: date
    status: DayStatus
    working_kind: WorkingKind | None


@dataclass(frozen=True, slots=True)
class CoverageConflict:
    """A single AC-02 violation."""

    code: str
    message: str
    store_id: str | None = None
    person_id: str | None = None
    business_date: date | None = None


def check_coverage(assignments: list[SiteDayAssignment]) -> list[CoverageConflict]:
    """Return the list of AC-02 violations for the given assignments."""

    conflicts: list[CoverageConflict] = []

    # Index 1: store + business_date -> list of working assignments.
    by_store_day: dict[tuple[str, date], list[SiteDayAssignment]] = {}
    for a in assignments:
        if a.status != DayStatus.WORKING:
            continue
        key = (a.store_id, a.business_date)
        by_store_day.setdefault(key, []).append(a)

    for (store_id, business_date), group in by_store_day.items():
        if len(group) > 1:
            conflicts.append(
                CoverageConflict(
                    code="MULTIPLE_AGENTS_PER_STORE_DAY",
                    message=(
                        f"Store {store_id} has {len(group)} working agents on "
                        f"{business_date.isoformat()}; expected exactly one."
                    ),
                    store_id=store_id,
                    business_date=business_date,
                )
            )

    # Index 2: person + business_date -> set of distinct store_ids.
    by_person_day: dict[tuple[str, date], set[str]] = {}
    for a in assignments:
        if a.status != DayStatus.WORKING:
            continue
        key = (a.person_id, a.business_date)
        by_person_day.setdefault(key, set()).add(a.store_id)

    for (person_id, business_date), store_ids in by_person_day.items():
        if len(store_ids) > 1:
            conflicts.append(
                CoverageConflict(
                    code="MULTIPLE_STORES_PER_AGENT_DAY",
                    message=(
                        f"Person {person_id} works in {len(store_ids)} stores on "
                        f"{business_date.isoformat()}; expected exactly one."
                    ),
                    person_id=person_id,
                    business_date=business_date,
                )
            )

    return conflicts


def assert_coverage(assignments: list[SiteDayAssignment]) -> None:
    """Raise :class:`CoverageInvariantError` if any AC-02 violation is present."""

    conflicts = check_coverage(assignments)
    if conflicts:
        from .errors import CoverageInvariantError

        raise CoverageInvariantError(
            f"AC-02 violations: {len(conflicts)}",
            details={
                "conflicts": [
                    {
                        "code": c.code,
                        "message": c.message,
                        "store_id": c.store_id,
                        "person_id": c.person_id,
                        "business_date": c.business_date.isoformat() if c.business_date else None,
                    }
                    for c in conflicts
                ]
            },
        )


def validate_working_kind(
    *,
    person_home_store_id: str,
    site_store_id: str,
    working_kind: WorkingKind,
) -> None:
    """Enforce EXTRA_HOME / EXTRA_OTHER preconditions per ARCHITECTURE §5."""

    from .errors import ValidationError

    if working_kind == WorkingKind.EXTRA_HOME and person_home_store_id != site_store_id:
        raise ValidationError(
            "EXTRA_HOME requires person.home_store_id == site.store_id",
            details={
                "working_kind": working_kind.value,
                "person_home_store_id": person_home_store_id,
                "site_store_id": site_store_id,
            },
        )
    if working_kind == WorkingKind.EXTRA_OTHER and person_home_store_id == site_store_id:
        raise ValidationError(
            "EXTRA_OTHER requires person.home_store_id != site.store_id",
            details={
                "working_kind": working_kind.value,
                "person_home_store_id": person_home_store_id,
                "site_store_id": site_store_id,
            },
        )
