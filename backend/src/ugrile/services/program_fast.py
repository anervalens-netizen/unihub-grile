"""Indexed Program read-model builder for the primary manager calendar grid.

This preserves the existing ``ProgramService`` response contract while avoiding
repeated full-list scans for every store/person day cell. Assignment indexes use
``setdefault`` so duplicate/conflict data retains the legacy first-row behavior;
conflict detection remains owned by the existing validation/exception paths.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..repositories.models import Month, Person, PersonDayAbsence, SiteDayAssignment, Store
from .overview import ProgramCell, ProgramGrid, ProgramRow


class IndexedProgramService:
    """Build the complete Program lattice with O(1) cell lookups."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def month_grid(
        self,
        *,
        tenant_id: str,
        month: Month,
        perspective: str,
        manager_scope_store_ids: Mapping[date, set[str]] | None,
    ) -> ProgramGrid:
        if perspective not in {"stores", "people"}:
            raise ValueError("perspective must be 'stores' or 'people'")

        days = monthrange(month.year, month.month)[1]
        dates = tuple(date(month.year, month.month, 1 + offset) for offset in range(days))
        stores = list(
            self.session.execute(
                select(Store).where(Store.tenant_id == tenant_id, Store.is_active.is_(True))
            ).scalars()
        )
        people = list(
            self.session.execute(
                select(Person).where(Person.tenant_id == tenant_id, Person.is_active.is_(True))
            ).scalars()
        )
        assignments = list(
            self.session.execute(
                select(SiteDayAssignment).where(
                    SiteDayAssignment.tenant_id == tenant_id,
                    SiteDayAssignment.month_id == month.id,
                )
            ).scalars()
        )
        absences = list(
            self.session.execute(
                select(PersonDayAbsence).where(
                    PersonDayAbsence.tenant_id == tenant_id,
                    PersonDayAbsence.month_id == month.id,
                )
            ).scalars()
        )

        person_by_id = {person.id: person for person in people}
        store_by_id = {store.id: store for store in stores}
        assignment_by_store_date: dict[tuple[str, date], SiteDayAssignment] = {}
        assignment_by_person_date: dict[tuple[str, date], SiteDayAssignment] = {}
        for row in assignments:
            assignment_by_store_date.setdefault((row.store_id, row.business_date), row)
            assignment_by_person_date.setdefault((row.person_id, row.business_date), row)
        absence_person_dates = {(row.person_id, row.business_date) for row in absences}

        rows: list[ProgramRow] = []
        if perspective == "stores":
            visible_stores = [
                store
                for store in stores
                if manager_scope_store_ids is None
                or any(store.id in manager_scope_store_ids[d] for d in dates)
            ]
            for store in sorted(visible_stores, key=lambda item: item.internal_code):
                cells: list[ProgramCell] = []
                for business_date in dates:
                    locked = (
                        manager_scope_store_ids is not None
                        and store.id not in manager_scope_store_ids.get(business_date, set())
                    )
                    match = assignment_by_store_date.get((store.id, business_date))
                    if match is not None:
                        person = person_by_id.get(match.person_id)
                        cells.append(
                            ProgramCell(
                                business_date=business_date,
                                person_id=match.person_id,
                                store_id=match.store_id,
                                status=match.status,
                                working_kind=match.working_kind,
                                display_name=person.display_name if person else None,
                                home_store_id=person.home_store_id if person else None,
                                badge=_badge_for(match.working_kind),
                                locked=locked,
                            )
                        )
                    else:
                        # Store coverage is uncovered whether the non-working
                        # reason is an absence or no person-day row at all.
                        cells.append(
                            ProgramCell(
                                business_date=business_date,
                                person_id=None,
                                store_id=store.id,
                                status="UNCOVERED",
                                working_kind=None,
                                display_name=None,
                                home_store_id=None,
                                badge="UNCOVERED",
                                locked=locked,
                            )
                        )
                rows.append(
                    ProgramRow(
                        row_id=store.id,
                        label=f"{store.internal_code} · {store.name}",
                        home_store_id=store.id,
                        cells=tuple(cells),
                    )
                )
        else:
            visible_people = [
                person
                for person in people
                if manager_scope_store_ids is None
                or any(person.home_store_id in manager_scope_store_ids[d] for d in dates)
            ]
            for person in sorted(visible_people, key=lambda item: item.internal_code):
                cells: list[ProgramCell] = []
                for business_date in dates:
                    locked = (
                        manager_scope_store_ids is not None
                        and person.home_store_id
                        not in manager_scope_store_ids.get(business_date, set())
                    )
                    match = assignment_by_person_date.get((person.id, business_date))
                    if match is not None:
                        store_obj = store_by_id.get(match.store_id)
                        store_label = (
                            store_obj.internal_code if store_obj is not None else match.store_id
                        )
                        cells.append(
                            ProgramCell(
                                business_date=business_date,
                                person_id=match.person_id,
                                store_id=match.store_id,
                                status=match.status,
                                working_kind=match.working_kind,
                                display_name=store_label,
                                home_store_id=person.home_store_id,
                                badge=_badge_for(match.working_kind),
                                locked=locked,
                            )
                        )
                    elif (person.id, business_date) in absence_person_dates:
                        cells.append(
                            ProgramCell(
                                business_date=business_date,
                                person_id=person.id,
                                store_id=None,
                                status="OFF_OR_LEAVE",
                                working_kind=None,
                                display_name=None,
                                home_store_id=person.home_store_id,
                                badge="OFF",
                                locked=locked,
                            )
                        )
                    else:
                        cells.append(
                            ProgramCell(
                                business_date=business_date,
                                person_id=person.id,
                                store_id=None,
                                status="OFF_OR_LEAVE",
                                working_kind=None,
                                display_name=None,
                                home_store_id=person.home_store_id,
                                badge="LIBER",
                                locked=locked,
                            )
                        )
                rows.append(
                    ProgramRow(
                        row_id=person.id,
                        label=f"{person.internal_code} · {person.display_name}",
                        home_store_id=person.home_store_id,
                        cells=tuple(cells),
                    )
                )

        return ProgramGrid(
            month_id=month.id,
            year=month.year,
            month=month.month,
            revision=month.revision,
            dates=dates,
            rows=tuple(rows),
            legend=("NORMAL", "EXTRA_HOME", "EXTRA_OTHER", "LIBER", "CONCEDIU", "BLOCAT"),
        )


def _badge_for(working_kind: str | None) -> str | None:
    if working_kind is None:
        return None
    if working_kind == "NORMAL":
        return "NORMAL"
    if working_kind == "EXTRA_HOME":
        return "EXTRA_HOME"
    if working_kind == "EXTRA_OTHER":
        return "EXTRA_OTHER"
    return working_kind


__all__ = ["IndexedProgramService"]
