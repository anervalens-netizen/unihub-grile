"""Revisioned calendar application service; the calendar is the sole authority."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from uuid import uuid4

from sqlalchemy import delete, insert, select
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
from .attribution import AttributionService
from .audit import record_audit_event
from .month_participants import month_participant_ids
from .person_scope import effective_home_store_map


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

    @staticmethod
    def _audit_value(change: CalendarChange | None) -> dict[str, object] | None:
        if change is None:
            return None
        return {
            "person_id": change.person_id,
            "business_date": change.business_date.isoformat(),
            "store_id": change.store_id,
            "status": change.status.value,
            "working_kind": change.working_kind.value if change.working_kind else None,
        }

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
        actor_id: str | None = None,
        source: str = "SYSTEM",
        correlation_id: str | None = None,
    ) -> CalendarResult:
        """Run exact apply validation in a rolled-back savepoint.

        The real apply path also creates its audit row, but the savepoint is
        deliberately rolled back so preview never leaves mutation evidence.
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
                    actor_id=actor_id,
                    source=source,
                    correlation_id=correlation_id,
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
        actor_id: str | None = None,
        source: str = "SYSTEM",
        correlation_id: str | None = None,
    ) -> CalendarResult:
        month = self.session.execute(
            select(Month).where(Month.id == month.id).with_for_update()
        ).scalar_one()
        if month.tenant_id != tenant_id:
            raise ScopeError("month belongs to another tenant")
        if month.state == MonthState.CLOSED.value:
            raise ConflictError(
                "month is closed",
                details={"code": "MONTH_CLOSED", "month_id": month.id},
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
            change.business_date.year != month.year
            or change.business_date.month != month.month
            for change in changes
        ):
            raise ValidationError(
                "calendar change is outside the requested month",
                details={"month_id": month.id},
            )
        keys = [(change.person_id, change.business_date) for change in changes]
        if len(keys) != len(set(keys)):
            raise ValidationError(
                "calendar contains duplicate person/day changes",
                details={"duplicates": sorted({key for key in keys if keys.count(key) > 1})},
            )

        person_ids = {change.person_id for change in changes}
        store_ids = {change.store_id for change in changes if change.store_id}
        people = {
            person.id: person
            for person in self.session.execute(
                select(Person).where(
                    Person.tenant_id == tenant_id,
                    Person.id.in_(person_ids),
                )
            ).scalars()
        }
        stores = {
            store.id: store
            for store in self.session.execute(
                select(Store).where(
                    Store.tenant_id == tenant_id,
                    Store.id.in_(store_ids),
                )
            ).scalars()
        }
        if len(people) != len(person_ids) or len(stores) != len(store_ids):
            raise ValidationError("unknown person or store technical id")

        effective_home = effective_home_store_map(
            self.session,
            tenant_id=tenant_id,
            person_ids=person_ids,
            business_dates={change.business_date for change in changes},
        )
        missing_home = sorted(
            (change.person_id, change.business_date.isoformat())
            for change in changes
            if (change.person_id, change.business_date) not in effective_home
        )
        if missing_home:
            raise ValidationError(
                "effective home store is unknown for one or more calendar changes",
                details={"person_dates": missing_home},
            )

        for change in changes:
            allowed: set[str] | None
            if allowed_store_ids_by_date is not None:
                allowed = allowed_store_ids_by_date.get(change.business_date, set())
            else:
                allowed = allowed_store_ids
            person_home_store_id = effective_home[(change.person_id, change.business_date)]
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

        for change in changes:
            if change.status == DayStatus.WORKING:
                if change.store_id is None or change.working_kind is None:
                    raise ValidationError("working change requires store and working_kind")
                validate_working_kind(
                    person_home_store_id=effective_home[
                        (change.person_id, change.business_date)
                    ],
                    site_store_id=change.store_id,
                    working_kind=change.working_kind,
                )

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
            (row.person_id, row.business_date): CalendarChange(
                row.person_id,
                row.business_date,
                row.store_id,
                DayStatus(row.status),
                WorkingKind(row.working_kind) if row.working_kind else None,
            )
            for row in existing
        }
        candidate.update(
            {
                (row.person_id, row.business_date): CalendarChange(
                    row.person_id,
                    row.business_date,
                    None,
                    DayStatus(row.status),
                    None,
                )
                for row in existing_absences
            }
        )
        before_by_key = {key: candidate.get(key) for key in keys}
        for change in changes:
            candidate[(change.person_id, change.business_date)] = change

        working = [
            SiteDayAssignment(
                change.store_id or "",
                change.person_id,
                change.business_date,
                change.status,
                change.working_kind,
            )
            for change in candidate.values()
            if change.status == DayStatus.WORKING
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

        revision_before = month.revision
        new_revision = revision_before + 1
        row_source = source[:32] or "SYSTEM"
        assignment_values: list[dict[str, object]] = []
        absence_values: list[dict[str, object]] = []
        for change in sorted(
            candidate.values(),
            key=lambda item: (item.business_date, item.person_id),
        ):
            if change.status == DayStatus.WORKING:
                assignment_values.append(
                    {
                        "tenant_id": tenant_id,
                        "month_id": month.id,
                        "store_id": change.store_id or "",
                        "person_id": change.person_id,
                        "business_date": change.business_date,
                        "status": change.status.value,
                        "working_kind": (
                            change.working_kind.value if change.working_kind else None
                        ),
                        "revision": new_revision,
                        "source": row_source,
                    }
                )
            else:
                absence_values.append(
                    {
                        "tenant_id": tenant_id,
                        "month_id": month.id,
                        "person_id": change.person_id,
                        "business_date": change.business_date,
                        "status": change.status.value,
                    }
                )
        if assignment_values:
            self.session.execute(insert(AssignmentRow), assignment_values)
        if absence_values:
            self.session.execute(insert(PersonDayAbsence), absence_values)
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
                row.store_id,
                row.person_id,
                row.business_date,
                DayStatus(row.status),
                WorkingKind(row.working_kind) if row.working_kind else None,
            )
            for row in rows
        ] + [
            SiteDayAssignment(
                "",
                row.person_id,
                row.business_date,
                DayStatus(row.status),
                None,
            )
            for row in absence_rows
        ]

        dates = [
            date(month.year, month.month, day)
            for day in range(1, monthrange(month.year, month.month)[1] + 1)
        ]
        participants = sorted(
            month_participant_ids(
                self.session,
                tenant_id=tenant_id,
                month=month,
            )
        )
        person_cal = derive_person_calendar(domains, participants, dates)
        pontaj = derive_pontaj(person_cal, hours)
        self._materialize_pontaj(
            tenant_id=tenant_id,
            month=month,
            revision=new_revision,
            rows=pontaj,
        )
        AttributionService(self.session).rebuild_for_month(
            month=month,
            tenant_id=tenant_id,
            revision=new_revision,
        )

        audit_correlation_id = correlation_id or uuid4().hex
        record_audit_event(
            self.session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="CALENDAR_APPLY",
            entity="month",
            entity_id=month.id,
            payload={
                "month_id": month.id,
                "revision_before": revision_before,
                "revision_after": new_revision,
                "source": source,
                "correlation_id": audit_correlation_id,
                "changes": [
                    {
                        "person_id": change.person_id,
                        "business_date": change.business_date.isoformat(),
                        "before": self._audit_value(
                            before_by_key.get((change.person_id, change.business_date))
                        ),
                        "after": self._audit_value(
                            candidate[(change.person_id, change.business_date)]
                        ),
                    }
                    for change in changes
                ],
            },
        )

        stores_all = sorted(
            {
                store.id
                for store in self.session.execute(
                    select(Store).where(
                        Store.tenant_id == tenant_id,
                        Store.is_active.is_(True),
                    )
                ).scalars()
            }
        )
        return CalendarResult(
            month.id,
            new_revision,
            rows,
            person_cal,
            derive_store_coverage(domains, stores_all, dates),
            pontaj,
        )

    def _materialize_pontaj(
        self,
        *,
        tenant_id: str,
        month: Month,
        revision: int,
        rows: list[PontajDay],
    ) -> None:
        """Persist one complete immutable Pontaj revision efficiently."""

        values = [
            {
                "tenant_id": tenant_id,
                "month_id": month.id,
                "person_id": row.person_id,
                "business_date": row.business_date,
                "revision": revision,
                "status": row.status.value,
                "start_time": row.start,
                "end_time": row.end,
                "pause_minutes": row.pause_minutes,
                "hours": row.hours,
            }
            for row in rows
        ]
        # Ten bound columns per row. 3,000 rows stays below PostgreSQL's
        # parameter ceiling while allowing the 80/160-person realistic fixtures
        # to materialize in one or two statements instead of ten-plus chunks.
        chunk_size = 3000
        for start in range(0, len(values), chunk_size):
            self.session.execute(
                insert(PontajProjection).values(values[start : start + chunk_size])
            )
