"""Revisioned calendar application service; the calendar is the sole authority."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..domain.calendar import SiteDayAssignment, validate_working_kind
from ..domain.enums import DayStatus, MonthState, WorkingKind
from ..domain.errors import ConflictError, ScopeError, StaleRevisionError, ValidationError
from ..domain.projections import (
    HoursConfig,
    PersonCalendarDay,
    PontajDay,
    StoreCoverageDay,
    derive_person_calendar,
    derive_pontaj,
    derive_store_coverage,
)
from ..repositories.models import Month, Person, PersonDayAbsence, PontajProjection, Store
from ..repositories.models import SiteDayAssignment as AssignmentRow


@dataclass(frozen=True, slots=True)
class CalendarChange:
    person_id: str
    business_date: date
    store_id: str | None
    status: DayStatus
    working_kind: WorkingKind | None = None


@dataclass(frozen=True, slots=True)
class CalendarResult:
    month_id: str
    revision: int
    assignments: list[AssignmentRow]
    person_calendar: list[PersonCalendarDay]
    coverage: list[StoreCoverageDay]
    pontaj: list[PontajDay]


class _PreviewRollback(Exception):
    def __init__(self, result: CalendarResult) -> None:
        super().__init__("preview rollback")
        self.result = result


class CalendarService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def preview(
        self,
        *,
        month: Month,
        tenant_id: str,
        changes: list[CalendarChange],
        expected_revision: int,
        allowed_store_ids: set[str] | None = None,
        allowed_store_ids_by_date: Mapping[date, set[str]] | None = None,
        hours: HoursConfig = HoursConfig(),
    ) -> CalendarResult:
        """Run the exact apply validation in a rolled-back savepoint.

        This keeps XLSX preview and apply on one validation path, including
        coverage, working-kind, scope and tenant checks, without persisting any
        calendar row or revision.
        """

        try:
            with self.session.begin_nested():
                result = self.apply(
                    month=month,
                    tenant_id=tenant_id,
                    changes=changes,
                    expected_revision=expected_revision,
                    allowed_store_ids=allowed_store_ids,
                    allowed_store_ids_by_date=allowed_store_ids_by_date,
                    hours=hours,
                )
                raise _PreviewRollback(result)
        except _PreviewRollback as rollback:
            return rollback.result

    def apply(
        self,
        *,
        month: Month,
        tenant_id: str,
        changes: list[CalendarChange],
        expected_revision: int,
        allowed_store_ids: set[str] | None = None,
        allowed_store_ids_by_date: Mapping[date, set[str]] | None = None,
        hours: HoursConfig = HoursConfig(),
    ) -> CalendarResult:
        # Lock the month row so two managers cannot both pass the same CAS
        # revision and overwrite one another's calendar transaction.
        month = self.session.execute(
            select(Month).where(Month.id == month.id).with_for_update()
        ).scalar_one()
        if month.tenant_id != tenant_id:
            raise ScopeError("month belongs to another tenant")
        if month.state == MonthState.CLOSED.value:
            raise ConflictError(
                "month is closed", details={"code": "MONTH_CLOSED", "month_id": month.id}
            )
        if month.revision != expected_revision:
            raise StaleRevisionError(
                "stale calendar revision",
                details={
                    "code": "STALE_REVISION",
                    "expected": expected_revision,
                    "current": month.revision,
                },
            )
        if any(
            c.business_date.year != month.year or c.business_date.month != month.month
            for c in changes
        ):
            raise ValidationError(
                "calendar change is outside the requested month",
                details={"month_id": month.id},
            )
        keys = [(c.person_id, c.business_date) for c in changes]
        if len(keys) != len(set(keys)):
            raise ValidationError(
                "calendar contains duplicate person/day changes",
                details={"duplicates": sorted({key for key in keys if keys.count(key) > 1})},
            )
        person_ids = {c.person_id for c in changes}
        store_ids = {c.store_id for c in changes if c.store_id}
        people = {
            p.id: p
            for p in self.session.execute(
                select(Person).where(Person.tenant_id == tenant_id, Person.id.in_(person_ids))
            ).scalars()
        }
        stores = {
            s.id: s
            for s in self.session.execute(
                select(Store).where(Store.tenant_id == tenant_id, Store.id.in_(store_ids))
            ).scalars()
        }
        if len(people) != len(person_ids) or len(stores) != len(store_ids):
            raise ValidationError("unknown person or store technical id")
        for change in changes:
            allowed: set[str] | None
            if allowed_store_ids_by_date is not None:
                allowed = allowed_store_ids_by_date.get(change.business_date, set())
            else:
                allowed = allowed_store_ids
            person_home_store_id = people[change.person_id].home_store_id
            if allowed is not None and person_home_store_id not in allowed:
                raise ScopeError(
                    "calendar person is outside manager scope",
                    details={
                        "person_id": change.person_id,
                        "store_id": person_home_store_id,
                        "business_date": change.business_date.isoformat(),
                    },
                )
            if (
                change.status == DayStatus.WORKING
                and change.store_id is not None
                and allowed is not None
                and change.store_id not in allowed
            ):
                raise ScopeError(
                    "calendar store is outside manager scope",
                    details={
                        "store_id": change.store_id,
                        "business_date": change.business_date.isoformat(),
                    },
                )
        for c in changes:
            if c.status == DayStatus.WORKING:
                if c.store_id is None or c.working_kind is None:
                    raise ValidationError("working change requires store and working_kind")
                validate_working_kind(
                    person_home_store_id=people[c.person_id].home_store_id,
                    site_store_id=c.store_id,
                    working_kind=c.working_kind,
                )
        # Build the candidate whole month before touching rows, so invalid/conflicting files roll back fully.
        existing = list(
            self.session.execute(
                select(AssignmentRow).where(
                    AssignmentRow.tenant_id == tenant_id,
                    AssignmentRow.month_id == month.id,
                )
            ).scalars()
        )
        existing_absences = list(
            self.session.execute(
                select(PersonDayAbsence).where(
                    PersonDayAbsence.tenant_id == tenant_id,
                    PersonDayAbsence.month_id == month.id,
                )
            ).scalars()
        )
        candidate = {
            (r.person_id, r.business_date): CalendarChange(
                r.person_id,
                r.business_date,
                r.store_id,
                DayStatus(r.status),
                WorkingKind(r.working_kind) if r.working_kind else None,
            )
            for r in existing
        }
        candidate.update(
            {
                (r.person_id, r.business_date): CalendarChange(
                    r.person_id, r.business_date, None, DayStatus(r.status), None
                )
                for r in existing_absences
            }
        )
        for c in changes:
            candidate[(c.person_id, c.business_date)] = c
        working = [
            SiteDayAssignment(
                c.store_id or "", c.person_id, c.business_date, c.status, c.working_kind
            )
            for c in candidate.values()
            if c.status == DayStatus.WORKING
        ]
        from ..domain.calendar import assert_coverage

        assert_coverage(working)
        self.session.execute(
            delete(AssignmentRow).where(
                AssignmentRow.tenant_id == tenant_id,
                AssignmentRow.month_id == month.id,
            )
        )
        self.session.execute(
            delete(PersonDayAbsence).where(
                PersonDayAbsence.tenant_id == tenant_id,
                PersonDayAbsence.month_id == month.id,
            )
        )
        new_revision = month.revision + 1
        for c in sorted(candidate.values(), key=lambda x: (x.business_date, x.person_id)):
            if c.status == DayStatus.WORKING:
                self.session.add(
                    AssignmentRow(
                        tenant_id=tenant_id,
                        month_id=month.id,
                        store_id=c.store_id or "",
                        person_id=c.person_id,
                        business_date=c.business_date,
                        status=c.status.value,
                        working_kind=c.working_kind.value if c.working_kind else None,
                        revision=new_revision,
                        source="CALENDAR",
                    )
                )
            else:
                self.session.add(
                    PersonDayAbsence(
                        tenant_id=tenant_id,
                        month_id=month.id,
                        person_id=c.person_id,
                        business_date=c.business_date,
                        status=c.status.value,
                    )
                )
        month.revision = new_revision
        self.session.flush()
        rows = list(
            self.session.execute(
                select(AssignmentRow).where(
                    AssignmentRow.tenant_id == tenant_id,
                    AssignmentRow.month_id == month.id,
                )
            ).scalars()
        )
        absence_rows = list(
            self.session.execute(
                select(PersonDayAbsence).where(
                    PersonDayAbsence.tenant_id == tenant_id,
                    PersonDayAbsence.month_id == month.id,
                )
            ).scalars()
        )
        domains = [
            SiteDayAssignment(
                r.store_id,
                r.person_id,
                r.business_date,
                DayStatus(r.status),
                WorkingKind(r.working_kind) if r.working_kind else None,
            )
            for r in rows
        ] + [
            SiteDayAssignment("", r.person_id, r.business_date, DayStatus(r.status), None)
            for r in absence_rows
        ]
        self._materialize_pontaj(
            tenant_id=tenant_id,
            month=month,
            revision=new_revision,
            domains=domains,
            hours=hours,
        )
        dates = [
            date(month.year, month.month, day)
            for day in range(1, monthrange(month.year, month.month)[1] + 1)
        ]
        people_all = sorted(
            {
                p.id
                for p in self.session.execute(
                    select(Person).where(Person.tenant_id == tenant_id, Person.is_active.is_(True))
                ).scalars()
            }
        )
        stores_all = sorted(
            {
                s.id
                for s in self.session.execute(
                    select(Store).where(Store.tenant_id == tenant_id, Store.is_active.is_(True))
                ).scalars()
            }
        )
        person_cal = derive_person_calendar(domains, people_all, dates)
        return CalendarResult(
            month.id,
            new_revision,
            rows,
            person_cal,
            derive_store_coverage(domains, stores_all, dates),
            derive_pontaj(person_cal, hours),
        )

    def _materialize_pontaj(
        self,
        *,
        tenant_id: str,
        month: Month,
        revision: int,
        domains: list[SiteDayAssignment],
        hours: HoursConfig,
    ) -> None:
        """Persist the full active-person x every-day-of-month Pontaj lattice.

        Runs inside the caller's transaction right after the calendar CAS, so a
        failed or rolled-back apply never leaves a projection behind. Rows are
        immutable per ``(tenant, month, person, business_date, revision)``:
        later revisions insert new rows and never touch earlier ones.
        """

        all_days = [
            date(month.year, month.month, day)
            for day in range(1, monthrange(month.year, month.month)[1] + 1)
        ]
        active_people = sorted(
            {
                p.id
                for p in self.session.execute(
                    select(Person).where(Person.tenant_id == tenant_id, Person.is_active.is_(True))
                ).scalars()
            }
        )
        for row in derive_pontaj(derive_person_calendar(domains, active_people, all_days), hours):
            self.session.add(
                PontajProjection(
                    tenant_id=tenant_id,
                    month_id=month.id,
                    person_id=row.person_id,
                    business_date=row.business_date,
                    revision=revision,
                    status=row.status.value,
                    start_time=row.start,
                    end_time=row.end,
                    pause_minutes=row.pause_minutes,
                    hours=row.hours,
                )
            )
