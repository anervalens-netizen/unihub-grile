"""Effective-dated manager scoping for the S4 Program grid.

The legacy Program read model uses the mutable ``Person.home_store_id`` catalog
field. That is safe for an admin tenant-wide view, but not for historical
manager authorization after a person transfer. This adapter builds the full
read model once, then fail-closes every manager row/cell against both the
manager's effective store scope and the person's effective home store for the
business date.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

from sqlalchemy.orm import Session

from .overview import ProgramCell, ProgramGrid, ProgramRow, ProgramService
from .person_scope import effective_home_store_map


def _locked_store_cell(cell: ProgramCell, *, store_id: str) -> ProgramCell:
    return ProgramCell(
        business_date=cell.business_date,
        person_id=None,
        store_id=store_id,
        status="LOCKED",
        working_kind=None,
        display_name=None,
        home_store_id=None,
        badge="BLOCAT",
        locked=True,
    )


def _locked_person_cell(cell: ProgramCell, *, person_id: str) -> ProgramCell:
    return ProgramCell(
        business_date=cell.business_date,
        person_id=person_id,
        store_id=None,
        status="LOCKED",
        working_kind=None,
        display_name=None,
        home_store_id=None,
        badge="BLOCAT",
        locked=True,
    )


class ScopedProgramService:
    """Return Program rows without historical person/store scope leakage."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._base = ProgramService(session)

    def month_grid(
        self,
        *,
        tenant_id: str,
        month,
        perspective: str,
        manager_scope_store_ids: Mapping[date, set[str]] | None,
    ) -> ProgramGrid:
        if manager_scope_store_ids is None:
            return self._base.month_grid(
                tenant_id=tenant_id,
                month=month,
                perspective=perspective,
                manager_scope_store_ids=None,
            )

        # Build the complete tenant read model first. Authorization is applied
        # below from effective-dated state so mutable current-home values cannot
        # cause a historical row to be omitted before it can be evaluated.
        grid = self._base.month_grid(
            tenant_id=tenant_id,
            month=month,
            perspective=perspective,
            manager_scope_store_ids=None,
        )
        dates = set(grid.dates)
        if perspective == "people":
            person_ids = {row.row_id for row in grid.rows}
        else:
            person_ids = {
                cell.person_id
                for row in grid.rows
                for cell in row.cells
                if cell.person_id is not None
            }
        effective_home = effective_home_store_map(
            self.session,
            tenant_id=tenant_id,
            person_ids=person_ids,
            business_dates=dates,
        )

        if perspective == "stores":
            rows = self._store_rows(
                grid,
                manager_scope_store_ids=manager_scope_store_ids,
                effective_home=effective_home,
            )
        else:
            rows = self._person_rows(
                grid,
                manager_scope_store_ids=manager_scope_store_ids,
                effective_home=effective_home,
            )

        return ProgramGrid(
            month_id=grid.month_id,
            year=grid.year,
            month=grid.month,
            revision=grid.revision,
            dates=grid.dates,
            rows=rows,
            legend=grid.legend,
        )

    def _store_rows(
        self,
        grid: ProgramGrid,
        *,
        manager_scope_store_ids: Mapping[date, set[str]],
        effective_home: Mapping[tuple[str, date], str],
    ) -> tuple[ProgramRow, ...]:
        visible_rows: list[ProgramRow] = []
        for row in grid.rows:
            if not any(
                row.row_id in manager_scope_store_ids.get(business_date, set())
                for business_date in grid.dates
            ):
                continue

            cells: list[ProgramCell] = []
            for cell in row.cells:
                allowed = manager_scope_store_ids.get(cell.business_date, set())
                if row.row_id not in allowed:
                    cells.append(_locked_store_cell(cell, store_id=row.row_id))
                    continue
                if cell.person_id is not None:
                    home_store_id = effective_home.get((cell.person_id, cell.business_date))
                    if home_store_id not in allowed:
                        cells.append(_locked_store_cell(cell, store_id=row.row_id))
                        continue
                    cells.append(
                        ProgramCell(
                            business_date=cell.business_date,
                            person_id=cell.person_id,
                            store_id=cell.store_id,
                            status=cell.status,
                            working_kind=cell.working_kind,
                            display_name=cell.display_name,
                            home_store_id=home_store_id,
                            badge=cell.badge,
                            locked=False,
                        )
                    )
                    continue
                cells.append(
                    ProgramCell(
                        business_date=cell.business_date,
                        person_id=None,
                        store_id=cell.store_id,
                        status=cell.status,
                        working_kind=cell.working_kind,
                        display_name=cell.display_name,
                        home_store_id=None,
                        badge=cell.badge,
                        locked=False,
                    )
                )

            visible_rows.append(
                ProgramRow(
                    row_id=row.row_id,
                    label=row.label,
                    home_store_id=row.row_id,
                    cells=tuple(cells),
                )
            )
        return tuple(visible_rows)

    def _person_rows(
        self,
        grid: ProgramGrid,
        *,
        manager_scope_store_ids: Mapping[date, set[str]],
        effective_home: Mapping[tuple[str, date], str],
    ) -> tuple[ProgramRow, ...]:
        visible_rows: list[ProgramRow] = []
        for row in grid.rows:
            visible_homes = {
                home_store_id
                for business_date in grid.dates
                if (
                    (home_store_id := effective_home.get((row.row_id, business_date)))
                    is not None
                    and home_store_id
                    in manager_scope_store_ids.get(business_date, set())
                )
            }
            if not visible_homes:
                continue

            cells: list[ProgramCell] = []
            for cell in row.cells:
                allowed = manager_scope_store_ids.get(cell.business_date, set())
                home_store_id = effective_home.get((row.row_id, cell.business_date))
                if home_store_id not in allowed:
                    cells.append(_locked_person_cell(cell, person_id=row.row_id))
                    continue
                if cell.store_id is not None and cell.store_id not in allowed:
                    cells.append(_locked_person_cell(cell, person_id=row.row_id))
                    continue
                cells.append(
                    ProgramCell(
                        business_date=cell.business_date,
                        person_id=cell.person_id,
                        store_id=cell.store_id,
                        status=cell.status,
                        working_kind=cell.working_kind,
                        display_name=cell.display_name,
                        home_store_id=home_store_id,
                        badge=cell.badge,
                        locked=False,
                    )
                )

            visible_rows.append(
                ProgramRow(
                    row_id=row.row_id,
                    label=row.label,
                    home_store_id=(next(iter(visible_homes)) if len(visible_homes) == 1 else None),
                    cells=tuple(cells),
                )
            )
        return tuple(visible_rows)


__all__ = ["ScopedProgramService"]
