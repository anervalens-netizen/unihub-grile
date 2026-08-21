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
person, category)``. The close checklist surfaces
``EPAY_FRESH_READBACK_REQUIRED`` when there is no observation in the
month window for any agent/category that participates in the
calculation. The actual readback authority stays with the S5a admin
endpoint; S6 will replace it with the Google Sheet canary source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import EpayCategory
from ..domain.errors import NotFoundError, ValidationError
from ..repositories.models import (
    EpayObservation,
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
        # bool is a subclass of int but never a valid E-pay input.
        return None, str(raw), False
    if isinstance(raw, int):
        value = raw
        raw_text = str(value)
        if 0 <= value <= 10:
            return value, raw_text, True
        return None, raw_text, False
    if isinstance(raw, float):
        # A fraction (e.g. 1.5) is invalid even though float is a number.
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
    """One parsed E-pay observation result."""

    person_id: str
    category: str
    value: int | None
    raw_value: str | None
    is_valid: bool


@dataclass(frozen=True, slots=True)
class EpayReadbackResult:
    """Outcome of one readback call.

    The ``valid_count`` and ``invalid_count`` are exposed so the manager
    UI can render the audit summary; ``observed_at`` is the canonical
    timestamp used by the freshness check in the close checklist.
    """

    store_id: str
    month_id: str
    observed_at: datetime
    items: tuple[EpayReadbackItem, ...]
    valid_count: int
    invalid_count: int


@dataclass(frozen=True, slots=True)
class EpayFreshnessReport:
    """Summary used by the close checklist."""

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
    """Persist one readback call.

    ``observations`` is a list of dicts ``{person_id, category, value}``.
    ``category`` must be ``UNDER_50`` or ``AT_OR_OVER_50``; ``value`` is
    the raw input (integer 0..10, blank, text, fraction, negative, or
    >10). Invalid rows are persisted with ``is_valid=False`` and
    ``raw_value`` preserved; only valid rows feed the grid engine via
    :func:`ugrile.repositories.epay.latest_snapshot`.
    """

    if not isinstance(observations, list):
        raise ValidationError(
            "observations must be a list",
            details={"code": "EPAY_READBACK_INVALID", "received": type(observations).__name__},
        )
    _require_store(session, tenant_id=tenant_id, store_id=store_id)
    if not observations:
        raise ValidationError(
            "observations must contain at least one entry",
            details={"code": "EPAY_READBACK_EMPTY"},
        )

    items: list[EpayReadbackItem] = []
    valid_count = 0
    invalid_count = 0
    observed_at = _utcnow()
    seen: set[tuple[str, str]] = set()

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
                source="EPAY_READBACK",
                observed_at=observed_at,
            )
        )

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
    """Return whether the readback for ``store_id`` is fresh.

    The freshness window defaults to 24h; the caller (close checklist)
    uses the report to decide whether to surface the
    ``EPAY_FRESH_READBACK_REQUIRED`` blocker.
    """

    persons = _working_persons_for_store_month(
        session, tenant_id=tenant_id, month_id=month_id, store_id=store_id
    )
    expected_count = len(persons) * len(EpayCategory.__members__.values())
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
                EpayObservation.is_valid.is_(True),
                EpayObservation.observed_at >= threshold,
            )
        ).scalars()
    )
    fresh_pairs: set[tuple[str, str]] = set()
    for row in rows:
        fresh_pairs.add((row.person_id, row.category))
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
