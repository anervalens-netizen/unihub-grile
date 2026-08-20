"""Site-day assignment repository.

This module is the heart of AC-02 enforcement. The two key responsibilities:

1. Translate Postgres unique-index violations into
   :class:`CoverageInvariantError` (HTTP 409) with a precise payload.
2. Read and write site-day assignments while keeping the partial unique
   indexes authoritative — the application must not pre-filter, because
   transactions may interleave.

The domain helpers in :mod:`ugrile.domain.calendar` perform the same checks in
pure form so the API can fail fast with a deterministic message even when
validation runs before persistence.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..domain.calendar import (
    CoverageConflict,
    assert_coverage,
    check_coverage,
)
from ..domain.calendar import (
    SiteDayAssignment as DomainAssignment,
)
from ..domain.enums import DayStatus, WorkingKind
from ..domain.errors import CoverageInvariantError
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

    def upsert_working(
        self,
        *,
        tenant_id: str,
        month_id: str,
        store_id: str,
        person_id: str,
        business_date: date,
        working_kind: WorkingKind,
        source: str = "MANAGER_UI",
    ) -> SiteDayAssignment:
        existing = self._find_one(
            tenant_id=tenant_id,
            store_id=store_id,
            person_id=person_id,
            business_date=business_date,
        )
        if existing is None:
            row = SiteDayAssignment(
                tenant_id=tenant_id,
                month_id=month_id,
                store_id=store_id,
                person_id=person_id,
                business_date=business_date,
                status=DayStatus.WORKING,
                working_kind=working_kind.value,
                revision=0,
                source=source,
            )
            self.session.add(row)
        else:
            existing.status = DayStatus.WORKING
            existing.working_kind = working_kind.value
            existing.source = source
            row = existing
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            self._raise_coverage_conflict(
                tenant_id=tenant_id,
                store_id=store_id,
                person_id=person_id,
                business_date=business_date,
                working_kind=working_kind,
                integrity_error=exc,
            )
        return row

    def remove_for_day(
        self,
        *,
        tenant_id: str,
        month_id: str,
        store_id: str,
        person_id: str,
        business_date: date,
    ) -> int:
        rows = (
            self.session.execute(
                select(SiteDayAssignment).where(
                    SiteDayAssignment.tenant_id == tenant_id,
                    SiteDayAssignment.month_id == month_id,
                    SiteDayAssignment.store_id == store_id,
                    SiteDayAssignment.person_id == person_id,
                    SiteDayAssignment.business_date == business_date,
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            self.session.delete(row)
        self.session.flush()
        return len(rows)

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

    def _find_one(
        self,
        *,
        tenant_id: str,
        store_id: str,
        person_id: str,
        business_date: date,
    ) -> SiteDayAssignment | None:
        stmt = select(SiteDayAssignment).where(
            and_(
                SiteDayAssignment.tenant_id == tenant_id,
                SiteDayAssignment.store_id == store_id,
                SiteDayAssignment.person_id == person_id,
                SiteDayAssignment.business_date == business_date,
            )
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def _raise_coverage_conflict(
        self,
        *,
        tenant_id: str,
        store_id: str,
        person_id: str,
        business_date: date,
        working_kind: WorkingKind,
        integrity_error: IntegrityError | None = None,
    ) -> None:
        """Surface a partial-unique violation as ``CoverageInvariantError``.

        The violation can be one of two partial unique indexes. We try the
        ``IntegrityError`` constraint name first (PostgreSQL) and fall back
        to inspecting the current state of both invariants (SQLite does not
        expose the index name in its error).
        """

        index_name = None
        if integrity_error is not None:
            orig = getattr(integrity_error, "orig", None)
            diag = getattr(orig, "diag", None) if orig is not None else None
            index_name = getattr(diag, "constraint_name", None) if diag else None
            if index_name is None and orig is not None:
                message = str(orig)
                if "uq_site_day_one_working" in message:
                    index_name = "uq_site_day_one_working"
                elif "uq_person_day_one_working" in message:
                    index_name = "uq_person_day_one_working"

        # Read the COMMITTED state (after rollback). The caller tried to
        # insert/update (store_id, person_id, business_date) as WORKING; if
        # either invariant is already populated in the committed state, we
        # can name the violation precisely.
        store_day_rows = list(
            self.session.execute(
                select(SiteDayAssignment).where(
                    SiteDayAssignment.tenant_id == tenant_id,
                    SiteDayAssignment.store_id == store_id,
                    SiteDayAssignment.business_date == business_date,
                    SiteDayAssignment.status == DayStatus.WORKING,
                )
            ).scalars()
        )
        person_day_rows = list(
            self.session.execute(
                select(SiteDayAssignment).where(
                    SiteDayAssignment.tenant_id == tenant_id,
                    SiteDayAssignment.person_id == person_id,
                    SiteDayAssignment.business_date == business_date,
                    SiteDayAssignment.status == DayStatus.WORKING,
                )
            ).scalars()
        )

        conflicts: list[dict[str, object]] = []
        # The committed store-day row tells us whether the caller was trying
        # to add a second person to a store-day.
        store_day_persons = sorted({r.person_id for r in store_day_rows})
        person_day_stores = sorted({r.store_id for r in person_day_rows})

        if store_day_persons:
            persons_for_code = sorted({*store_day_persons, person_id})
            conflicts.append(
                {
                    "code": "MULTIPLE_AGENTS_PER_STORE_DAY",
                    "store_id": store_id,
                    "person_id": None,
                    "business_date": business_date.isoformat(),
                    "person_ids": persons_for_code,
                    "index": "uq_site_day_one_working",
                }
            )
        if person_day_stores:
            stores_for_code = sorted({*person_day_stores, store_id})
            conflicts.append(
                {
                    "code": "MULTIPLE_STORES_PER_AGENT_DAY",
                    "store_id": None,
                    "person_id": person_id,
                    "business_date": business_date.isoformat(),
                    "store_ids": stores_for_code,
                    "index": "uq_person_day_one_working",
                }
            )

        if not conflicts:
            # The DB rejected the insert/update but we cannot see the
            # offending state. Surface a generic conflict so the API returns
            # 409 instead of 500.
            conflicts.append(
                {
                    "code": "COVERAGE_RACE",
                    "store_id": store_id,
                    "person_id": person_id,
                    "business_date": business_date.isoformat(),
                    "working_kind": working_kind.value,
                    "index": index_name,
                }
            )

        raise CoverageInvariantError(
            "AC-02 coverage invariant violated",
            details={"conflicts": conflicts},
        )


__all__ = ["AssignmentRepository"]
