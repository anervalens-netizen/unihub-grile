"""Read/diagnostic repository for authoritative site-day assignments.

Calendar mutation belongs exclusively to :class:`CalendarService`: it owns
revision/CAS, complete Pontaj/attribution regeneration and transactional audit.
This repository therefore exposes only reads and coverage diagnostics over the
persisted authoritative rows.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.calendar import CoverageConflict, assert_coverage, check_coverage
from ..domain.calendar import SiteDayAssignment as DomainAssignment
from ..domain.enums import DayStatus, WorkingKind
from .models import SiteDayAssignment


class AssignmentRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_month(
        self, month_id: str, *, store_id: str | None = None
    ) -> list[SiteDayAssignment]:
        stmt = select(SiteDayAssignment).where(SiteDayAssignment.month_id == month_id)
        if store_id is not None:
            stmt = stmt.where(SiteDayAssignment.store_id == store_id)
        stmt = stmt.order_by(
            SiteDayAssignment.business_date,
            SiteDayAssignment.store_id,
            SiteDayAssignment.person_id,
        )
        return list(self.session.execute(stmt).scalars())

    def list_for_person(self, month_id: str, person_id: str) -> list[SiteDayAssignment]:
        stmt = (
            select(SiteDayAssignment)
            .where(
                SiteDayAssignment.month_id == month_id,
                SiteDayAssignment.person_id == person_id,
            )
            .order_by(SiteDayAssignment.business_date)
        )
        return list(self.session.execute(stmt).scalars())

    def assert_month_coverage(self, month_id: str) -> None:
        """Raise if the stored month has any AC-02 violations."""

        rows = self.list_for_month(month_id)
        projections = [
            DomainAssignment(
                store_id=row.store_id,
                person_id=row.person_id,
                business_date=row.business_date,
                status=DayStatus(row.status),
                working_kind=WorkingKind(row.working_kind) if row.working_kind else None,
            )
            for row in rows
            if row.status == DayStatus.WORKING
        ]
        assert_coverage(projections)

    def project_assignments(self, rows: Iterable[SiteDayAssignment]) -> list[DomainAssignment]:
        return [
            DomainAssignment(
                store_id=row.store_id,
                person_id=row.person_id,
                business_date=row.business_date,
                status=DayStatus(row.status),
                working_kind=WorkingKind(row.working_kind) if row.working_kind else None,
            )
            for row in rows
            if row.status == DayStatus.WORKING
        ]

    def check_month_coverage(self, month_id: str) -> list[CoverageConflict]:
        rows = self.list_for_month(month_id)
        return check_coverage(self.project_assignments(rows))


__all__ = ["AssignmentRepository"]
