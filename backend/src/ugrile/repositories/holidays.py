"""Versioned Romanian legal holiday calendar repository.

The holiday calendar is an informational marker in S3 (docs/MOBIUP_RULE_PACK.md
§9): the engine never derives schedule, Pontaj, target or pay from it. The
repository provides the typed read/upsert seams the grid service and the
minimal admin/read API use.

* ``upsert_calendar`` / ``upsert_override`` — admin writes, composite-unique
  on ``(tenant_id, version, business_date)``.
* ``markers_for_month`` — deterministic per-month markers (version, date,
  label, calendar active flag, override state/reason) ordered by
  ``(version, business_date)``; the grid serialises them into the canonical
  inputs so a calendar/override change deterministically changes the inputs
  hash without touching any financial output.

Tenant safety
-------------

All queries scope by ``tenant_id`` first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import HolidayCalendar, HolidayOverride


@dataclass(frozen=True, slots=True)
class HolidayMarker:
    """One versioned holiday marker for a business date.

    ``override_active`` / ``override_reason`` are ``None`` when no admin
    override exists for the date. Informational only — the engine never
    derives schedule, Pontaj, target or pay from it.
    """

    version: str
    business_date: date
    label: str
    is_active: bool
    override_active: bool | None
    override_reason: str | None


class HolidayRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_calendar(
        self,
        *,
        tenant_id: str,
        version: str,
        business_date: date,
        label: str,
        is_active: bool = True,
    ) -> HolidayCalendar:
        existing = self.session.execute(
            select(HolidayCalendar).where(
                HolidayCalendar.tenant_id == tenant_id,
                HolidayCalendar.version == version,
                HolidayCalendar.business_date == business_date,
            )
        ).scalar_one_or_none()
        if existing is None:
            row = HolidayCalendar(
                tenant_id=tenant_id,
                version=version,
                business_date=business_date,
                label=label,
                is_active=is_active,
            )
            self.session.add(row)
            self.session.flush()
            return row
        existing.label = label
        existing.is_active = is_active
        self.session.flush()
        return existing

    def upsert_override(
        self,
        *,
        tenant_id: str,
        version: str,
        business_date: date,
        is_active: bool,
        reason: str,
        actor_id: str,
    ) -> HolidayOverride:
        existing = self.session.execute(
            select(HolidayOverride).where(
                HolidayOverride.tenant_id == tenant_id,
                HolidayOverride.version == version,
                HolidayOverride.business_date == business_date,
            )
        ).scalar_one_or_none()
        if existing is None:
            row = HolidayOverride(
                tenant_id=tenant_id,
                version=version,
                business_date=business_date,
                is_active=is_active,
                reason=reason,
                actor_id=actor_id,
            )
            self.session.add(row)
            self.session.flush()
            return row
        existing.is_active = is_active
        existing.reason = reason
        existing.actor_id = actor_id
        self.session.flush()
        return existing

    def markers_for_month(
        self,
        *,
        tenant_id: str,
        year: int,
        month: int,
    ) -> list[HolidayMarker]:
        """Return deterministic holiday markers for one calendar month.

        Each marker carries the versioned legal date plus the admin override
        state (``None`` when no override exists). Order is stable:
        ``(version, business_date)``.
        """

        first = date(year, month, 1)
        last = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        cal_rows = list(
            self.session.execute(
                select(HolidayCalendar)
                .where(
                    HolidayCalendar.tenant_id == tenant_id,
                    HolidayCalendar.business_date >= first,
                    HolidayCalendar.business_date < last,
                )
                .order_by(HolidayCalendar.version, HolidayCalendar.business_date)
            ).scalars()
        )
        overrides = list(
            self.session.execute(
                select(HolidayOverride)
                .where(
                    HolidayOverride.tenant_id == tenant_id,
                    HolidayOverride.business_date >= first,
                    HolidayOverride.business_date < last,
                )
                .order_by(HolidayOverride.version, HolidayOverride.business_date)
            ).scalars()
        )
        override_index: dict[tuple[str, date], HolidayOverride] = {
            (o.version, o.business_date): o for o in overrides
        }
        markers: list[HolidayMarker] = []
        for row in cal_rows:
            override = override_index.get((row.version, row.business_date))
            markers.append(
                HolidayMarker(
                    version=row.version,
                    business_date=row.business_date,
                    label=row.label,
                    is_active=row.is_active,
                    override_active=override.is_active if override else None,
                    override_reason=override.reason if override else None,
                )
            )
        return markers


__all__ = ["HolidayMarker", "HolidayRepository"]
