"""Pure calendar projections shared by API, XLSX and future read models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from .calendar import SiteDayAssignment
from .enums import DayStatus, WorkingKind


@dataclass(frozen=True, slots=True)
class HoursConfig:
    """Client-configured interval used by the derived Pontaj projection.

    The exact business interval is intentionally not hardcoded as a policy;
    S2 carries a safe default and validates any future tenant configuration.
    """

    start: time = time(10, 0)
    end: time = time(22, 0)
    pause_minutes: int = 60

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError("hours interval must end after it starts")
        if self.pause_minutes < 0:
            raise ValueError("pause_minutes must be non-negative")

    def worked_hours(self) -> Decimal:
        minutes = (
            datetime.combine(date.min, self.end) - datetime.combine(date.min, self.start)
        ).seconds // 60
        return Decimal(max(0, minutes - self.pause_minutes)) / Decimal(60)


@dataclass(frozen=True, slots=True)
class PersonCalendarDay:
    person_id: str
    business_date: date
    status: DayStatus
    store_id: str | None
    working_kind: WorkingKind | None


@dataclass(frozen=True, slots=True)
class StoreCoverageDay:
    store_id: str
    business_date: date
    person_id: str | None
    working_kind: WorkingKind | None
    covered: bool


@dataclass(frozen=True, slots=True)
class PontajDay:
    person_id: str
    business_date: date
    status: DayStatus
    start: time | None
    end: time | None
    pause_minutes: int
    hours: Decimal


def derive_person_calendar(
    assignments: list[SiteDayAssignment], person_ids: list[str], dates: list[date]
) -> list[PersonCalendarDay]:
    by_key = {(a.person_id, a.business_date): a for a in assignments}
    result: list[PersonCalendarDay] = []
    for person_id in sorted(person_ids):
        for day in dates:
            row = by_key.get((person_id, day))
            if row is None:
                result.append(PersonCalendarDay(person_id, day, DayStatus.OFF, None, None))
            elif row.status == DayStatus.WORKING:
                result.append(
                    PersonCalendarDay(
                        person_id, day, DayStatus.WORKING, row.store_id, row.working_kind
                    )
                )
            else:
                result.append(PersonCalendarDay(person_id, day, row.status, None, None))
    return result


def derive_store_coverage(
    assignments: list[SiteDayAssignment], store_ids: list[str], dates: list[date]
) -> list[StoreCoverageDay]:
    by_key = {
        (a.store_id, a.business_date): a for a in assignments if a.status == DayStatus.WORKING
    }
    return [
        StoreCoverageDay(
            store_id,
            day,
            (a.person_id if (a := by_key.get((store_id, day))) else None),
            (a.working_kind if a else None),
            a is not None,
        )
        for store_id in sorted(store_ids)
        for day in dates
    ]


def derive_pontaj(calendar: list[PersonCalendarDay], config: HoursConfig) -> list[PontajDay]:
    worked = config.worked_hours()
    return [
        PontajDay(
            c.person_id,
            c.business_date,
            c.status,
            config.start if c.status == DayStatus.WORKING else None,
            config.end if c.status == DayStatus.WORKING else None,
            config.pause_minutes if c.status == DayStatus.WORKING else 0,
            worked if c.status == DayStatus.WORKING else Decimal(0),
        )
        for c in calendar
    ]
