"""E-pay fresh readback service (AC-13 S5a slice).

The readback endpoint is admin-only and validates exactly four inputs
per store: two ``UNDER_50`` / ``AT_OR_OVER_50`` quantities per working
agent. Valid inputs are 0..10 integers; anything else (blank, text,
fraction, negative, >10) is recorded as ``is_valid=False`` with the
original raw_value preserved verbatim. Last-good retention is enforced
by the grid engine: only ``is_valid=True`` rows feed
:func:`ugrile.repositories.epay.latest_snapshot`.

Freshness contract
------------------

Each successful call records an ``observed_at`` per ``(tenant, store,
person, category)`` and a deterministic source discriminator bound to the
requested payroll month. The close checklist surfaces
``EPAY_FRESH_READBACK_REQUIRED`` when there is no observation in the freshness
window for any month-bound agent/category that participates in the calculation.
A recent readback from another month can never satisfy this check.

When a valid E-pay quantity actually changes, all current-revision grid rows
for the month are invalidated. A subsequent grid computation can therefore
replace the payroll snapshot without colliding with the same-revision unique
key. Refreshing identical E-pay values for freshness does not invalidate the
grid because the financial inputs are unchanged.

The month row is locked before readback state is inspected or observations are
written. This is the same serialization boundary used by final close, so a
concurrent readback can never mutate E-pay after close has validated an older
snapshot: either readback commits first and close sees the new state, or close
commits first and the readback observes ``CLOSED`` and fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import EpayCategory, MonthState
from ..domain.errors import ConflictError, NotFoundError, ValidationError
from ..domain.rule_pack import RULE_PACK_VERSION
from ..repositories.epay import latest_snapshot, month_source
from ..repositories.models import (
    EpayObservation,
    GridCalculation,
    Month,
    Person,
    SiteDayAssignment,
    Store,
)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _parse_quantity(raw: Any) -> tuple[int | None, str | None, bool]:
    """Parse a raw E-pay value into ``(value, normalized_raw, is_valid)``.

    The contract:

    * Accepts integers ``0..10`` inclusive — the only values that count
      as ``is_valid=True``.
    * Anything else (blank, text, fraction, negative, >10) becomes
      ``is_valid=False``; the original raw text is preserved as
      ``raw_value`` so the audit log can show exactly what was submitted.
    """

    if raw is None:
        return None, None, False
    if isinstance(raw, bool):
        return None, str(raw), False
    if isinstance(raw, int):
        value = raw
        raw_text = str(value)
        if 0 <= value <= 10:
            return value, raw_text, True
        return None, raw_text, False
    if isinstance(raw, float):
        raw_text = repr(raw)
        if raw.is_integer():
            value = int(raw)
            if 0 <= value <= 10:
                return value, raw_text, True
        return None, raw_text, False
    text = str(raw).strip()
    if not text:
        return None, "", False
    try:
        value = int(text)
    except ValueError:
        return None, text, False
    raw_text = text
    if 0 <= value <= 10:
        return value, raw_text, True
    return None, raw_text, False


@dataclass(frozen=True, slots=True)
class EpayReadbackItem:
    person_id: str
    category: str
    value: int | None
    raw_value: str | None
    is_valid: bool


@dataclass(frozen=True, slots=True)
class EpayReadbackResult:
    store_id: str
    month_id: str
    observed_at: datetime
    items: tuple[EpayReadbackItem, ...]
    valid_count: int
    invalid_count: int


@dataclass(frozen=True, slots=True)
class EpayFreshnessReport:
    is_fresh: bool
    fresh_count: int
    expected_count: int
    threshold: datetime


def _require_store(session: Session, *, tenant_id: str, store_id: str) -> Store:
    store = session.execute(
        select(Store).where(Store.tenant_id == tenant_id, Store.id == store_id)
    ).scalar_one_or_none()
    if store is None:
        raise NotFoundError(
            "store not found",
            details={"tenant_id": tenant_id, "store_id": store_id},
        )
    return store


def _lock_writable_month(session: Session, *, tenant_id: str, month_id: str) -> Month:
    month = session.execute(
        select(Month).where(Month.id == month_id).with_for_update()
    ).scalar_one_or_none()
    if month is None or month.tenant_id != tenant_id:
        raise NotFoundError(
            "month not found",
            details={"tenant_id": tenant_id, "month_id": month_id},
        )
    if month.state == MonthState.CLOSED.value:
        raise ConflictError(
            "month is closed",
            details={"code": "MONTH_CLOSED", "month_id": month.id},
        )
    return month


def _working_persons_for_store_month(
    session: Session, *, tenant_id: str, month_id: str, store_id: str
) -> list[str]:
    rows = list(
        session.execute(
            select(SiteDayAssignment).where(
                SiteDayAssignment.tenant_id == tenant_id,
                SiteDayAssignment.month_id == month_id,
                SiteDayAssignment.store_id == store_id,
                SiteDayAssignment.status == "WORKING",
            )
        ).scalars()
    )
    person_ids = sorted({row.person_id for row in rows})
    if person_ids:
        existing = {
            p.id
            for p in session.execute(
                select(Person).where(Person.tenant_id == tenant_id, Person.id.in_(person_ids))
            ).scalars()
        }
        person_ids = [pid for pid in person_ids if pid in existing]
    return person_ids


def record_readback(
    session: Session,
    *,
    tenant_id: str,
    month_id: str,
    store_id: str,
    observations: list[dict[str, Any]],
    actor_id: str,
) -> EpayReadbackResult:
    """Persist one serialized, month-bound readback call."""

    _ = actor_id
    if not isinstance(observations, list):
        raise ValidationError(
            "observations must be a list",
            details={"code": "EPAY_READBACK_INVALID", "received": type(observations).__name__},
        )
    _require_store(session, tenant_id=tenant_id, store_id=store_id)
    month = _lock_writable_month(session, tenant_id=tenant_id, month_id=month_id)
    working_persons = _working_persons_for_store_month(
        session, tenant_id=tenant_id, month_id=month_id, store_id=store_id
    )
    expected_pairs: set[tuple[str, str]] = {
        (pid, cat.value) for pid in working_persons for cat in EpayCategory
    }
    if not observations:
        raise ValidationError(
            "observations must contain at least one entry",
            details={"code": "EPAY_READBACK_EMPTY"},
        )
    if working_persons and len(observations) != len(expected_pairs):
        raise ValidationError(
            "observations must contain exactly one entry per (person, category) for working agents",
            details={
                "code": "EPAY_READBACK_FOUR_CELLS",
                "expected_count": len(expected_pairs),
                "received_count": len(observations),
            },
        )

    prior_values: dict[tuple[str, str], int] = {}
    for existing_person_id in working_persons:
        snapshot = latest_snapshot(
            session,
            tenant_id=tenant_id,
            month_id=month_id,
            store_id=store_id,
            person_id=existing_person_id,
        )
        prior_values[(existing_person_id, EpayCategory.UNDER_50.value)] = (
            snapshot.under_50_quantity
        )
        prior_values[(existing_person_id, EpayCategory.AT_OR_OVER_50.value)] = (
            snapshot.at_or_over_50_quantity
        )

    items: list[EpayReadbackItem] = []
    valid_count = 0
    invalid_count = 0
    observed_at = _utcnow()
    seen: set[tuple[str, str]] = set()
    source = month_source(month_id)
    grid_inputs_changed = False

    for entry in observations:
        if not isinstance(entry, dict):
            raise ValidationError(
                "each observation must be an object",
                details={"code": "EPAY_READBACK_INVALID"},
            )
        person_id = entry.get("person_id")
        category = entry.get("category")
        value = entry.get("value")
        if not isinstance(person_id, str) or not person_id:
            raise ValidationError(
                "observation.person_id is required",
                details={"code": "EPAY_READBACK_INVALID"},
            )
        if category not in {EpayCategory.UNDER_50.value, EpayCategory.AT_OR_OVER_50.value}:
            raise ValidationError(
                "observation.category must be UNDER_50 or AT_OR_OVER_50",
                details={
                    "code": "EPAY_READBACK_INVALID",
                    "person_id": person_id,
                    "category": category,
                },
            )
        if working_persons and (person_id, str(category)) not in expected_pairs:
            raise ValidationError(
                "person/category must belong to this store/month's working agents",
                details={
                    "code": "EPAY_READBACK_PERSON_SCOPE",
                    "person_id": person_id,
                    "category": str(category),
                    "store_id": store_id,
                    "month_id": month_id,
                },
            )
        key = (person_id, str(category))
        if key in seen:
            raise ValidationError(
                "duplicate observation for person/category",
                details={
                    "code": "EPAY_READBACK_DUPLICATE",
                    "person_id": person_id,
                    "category": category,
                },
            )
        seen.add(key)
        parsed_value, raw_text, is_valid = _parse_quantity(value)
        if is_valid:
            valid_count += 1
            prior = prior_values.get(key, 0)
            if parsed_value != prior:
                grid_inputs_changed = True
        else:
            invalid_count += 1
        items.append(
            EpayReadbackItem(
                person_id=person_id,
                category=str(category),
                value=parsed_value,
                raw_value=raw_text,
                is_valid=is_valid,
            )
        )
        session.add(
            EpayObservation(
                tenant_id=tenant_id,
                store_id=store_id,
                person_id=person_id,
                category=str(category),
                value=parsed_value,
                raw_value=raw_text,
                is_valid=is_valid,
                source=source,
                observed_at=observed_at,
            )
        )

    if grid_inputs_changed:
        session.query(GridCalculation).filter(
            GridCalculation.tenant_id == tenant_id,
            GridCalculation.month_id == month.id,
            GridCalculation.revision == month.revision,
            GridCalculation.rule_pack_version == RULE_PACK_VERSION,
        ).delete(synchronize_session=False)

    return EpayReadbackResult(
        store_id=store_id,
        month_id=month_id,
        observed_at=observed_at,
        items=tuple(items),
        valid_count=valid_count,
        invalid_count=invalid_count,
    )


def freshness_for_month(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    month_id: str,
    fresh_window_hours: int = 24,
) -> EpayFreshnessReport:
    """Return whether the exact month's store readback is fresh and complete."""

    persons = _working_persons_for_store_month(
        session, tenant_id=tenant_id, month_id=month_id, store_id=store_id
    )
    expected_count = len(persons) * len(EpayCategory)
    if expected_count == 0:
        return EpayFreshnessReport(
            is_fresh=True,
            fresh_count=0,
            expected_count=0,
            threshold=_utcnow() - timedelta(hours=fresh_window_hours),
        )
    threshold = _utcnow() - timedelta(hours=fresh_window_hours)
    rows = list(
        session.execute(
            select(EpayObservation).where(
                EpayObservation.tenant_id == tenant_id,
                EpayObservation.store_id == store_id,
                EpayObservation.person_id.in_(persons),
                EpayObservation.source == month_source(month_id),
                EpayObservation.is_valid.is_(True),
                EpayObservation.observed_at >= threshold,
            )
        ).scalars()
    )
    fresh_pairs = {(row.person_id, row.category) for row in rows}
    fresh_count = len(fresh_pairs)
    return EpayFreshnessReport(
        is_fresh=fresh_count >= expected_count,
        fresh_count=fresh_count,
        expected_count=expected_count,
        threshold=threshold,
    )


__all__ = [
    "EpayFreshnessReport",
    "EpayReadbackItem",
    "EpayReadbackResult",
    "freshness_for_month",
    "record_readback",
]
