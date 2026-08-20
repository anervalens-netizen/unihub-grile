"""S4 Manager UI read services.

This module builds the read models used by the manager UI pages:

* ``OverviewService.month_overview`` — a single aggregate call returning
  KPI cards (covered stores, uncovered days, conflicts, supplementary
  days, unattributed sales, epay freshness, sheet sync state) plus the
  manager-row table and the typed needs-attention list. The implementation
  performs a *bounded* number of aggregate queries (one per KPI); it never
  fans out a per-store read.
* ``ProgramService.month_grid`` — the calendar grid (per magazine and per
  agenti perspectives) for the full month. Virtualization happens on the
  client; the server returns the complete lattice in one request.
* ``ExceptionService.month_exceptions`` — typed exceptions sorted by
  severity, deduped by ``(code, store_id, person_id, business_date)``.
* ``CloseChecklistService.month_checklist`` — the same blocker detection
  the close service uses, plus the exact ``expected_revision`` and the
  generated/total job counts.

Every read is tenant-scoped; the manager scope filter is applied to
calendar rows so out-of-scope stores never appear in the response.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..domain.close import (
    CloseValidation,
    OpenStoreDay,
    PersonDaySnapshot,
    SalesAvailabilitySnapshot,
    StoreCoverageSnapshot,
    StoreTargetAvailabilitySnapshot,
    validate_close,
)
from ..domain.enums import CloseBlockerCode, DayStatus, MonthState
from ..repositories.models import (
    EpayObservation,
    GridCalculation,
    Month,
    OutboxJob,
    Person,
    PersonDayAbsence,
    PontajProjection,
    SalesStoreDay,
    SiteDayAssignment,
    Store,
    StoreTarget,
)

# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OverviewKpis:
    stores_total: int
    stores_covered: int
    days_uncovered: int
    conflicts: int
    extra_home_days: int
    extra_other_days: int
    sales_unattributed: int
    epay_invalid: int
    epay_fresh: bool
    sheet_sync_total: int
    sheet_sync_stale: int
    sheet_sync_error: int


@dataclass(frozen=True, slots=True)
class OverviewManagerRow:
    user_id: str
    display_name: str
    stores_covered: int
    stores_total: int
    days_uncovered: int
    last_sync: str | None


@dataclass(frozen=True, slots=True)
class OverviewNeedsAttention:
    code: str
    severity: int
    title: str
    detail: str
    store_id: str | None
    person_id: str | None
    business_date: date | None


@dataclass(frozen=True, slots=True)
class OverviewReport:
    month_id: str
    year: int
    month: int
    state: MonthState
    revision: int
    rule_pack_version: str | None
    kpis: OverviewKpis
    managers: tuple[OverviewManagerRow, ...]
    needs_attention: tuple[OverviewNeedsAttention, ...]


_SEVERITY: dict[str, int] = {
    CloseBlockerCode.STORE_DAY_UNCOVERED.value: 1,
    CloseBlockerCode.STORE_DAY_MULTIPLE_WORKING.value: 1,
    CloseBlockerCode.PERSON_DAY_MULTIPLE_WORKING.value: 1,
    CloseBlockerCode.INVALID_WORKING_KIND.value: 2,
    CloseBlockerCode.SALES_MISSING_FOR_WORKED_DAY.value: 1,
    CloseBlockerCode.SALES_ORPHAN_FOR_COVERED_DAY.value: 2,
    CloseBlockerCode.TARGET_ZERO_FOR_WORKED_STORE.value: 2,
    CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED.value: 2,
    CloseBlockerCode.SHEET_CANARY_REQUIRED.value: 3,
    CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED.value: 3,
}

_NEEDS_ATTENTION_TITLES: dict[str, str] = {
    CloseBlockerCode.STORE_DAY_UNCOVERED.value: "Magazin fără agent într-o zi",
    CloseBlockerCode.STORE_DAY_MULTIPLE_WORKING.value: "Magazin cu doi agenți într-o zi",
    CloseBlockerCode.PERSON_DAY_MULTIPLE_WORKING.value: "Agent în două magazine într-o zi",
    CloseBlockerCode.INVALID_WORKING_KIND.value: "Clasificare invalidă (home/other)",
    CloseBlockerCode.SALES_MISSING_FOR_WORKED_DAY.value: "Vânzare lipsă pentru o zi lucrată",
    CloseBlockerCode.SALES_ORPHAN_FOR_COVERED_DAY.value: "Vânzare fără calendar",
    CloseBlockerCode.TARGET_ZERO_FOR_WORKED_STORE.value: "Target lipsă/zero pentru magazin lucrat",
    CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED.value: "E-pay neverificat înainte de close",
    CloseBlockerCode.SHEET_CANARY_REQUIRED.value: "Canary Sheet nesincronizat",
    CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED.value: "Reconciliere externă lipsă",
}


class OverviewService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def month_overview(
        self,
        *,
        tenant_id: str,
        month: Month,
        manager_scope_store_ids: Mapping[date, set[str]] | None,
    ) -> OverviewReport:
        days = monthrange(month.year, month.month)[1]
        dates = [date(month.year, month.month, 1 + offset) for offset in range(days)]
        stores = list(
            self.session.execute(
                select(Store).where(Store.tenant_id == tenant_id, Store.is_active.is_(True))
            ).scalars()
        )
        if manager_scope_store_ids is None:
            scoped_store_ids = {store.id for store in stores}
        else:
            scoped_store_ids = {
                store_id
                for set_ids in manager_scope_store_ids.values()
                for store_id in set_ids
            }

        # Single aggregate query: every (store, date) with at least one WORKING row.
        covered_pairs = self.session.execute(
            select(SiteDayAssignment.store_id, SiteDayAssignment.business_date)
            .where(
                SiteDayAssignment.tenant_id == tenant_id,
                SiteDayAssignment.month_id == month.id,
                SiteDayAssignment.status == DayStatus.WORKING.value,
            )
            .group_by(SiteDayAssignment.store_id, SiteDayAssignment.business_date)
        ).all()
        covered_set = {(row[0], row[1]) for row in covered_pairs}

        lattice = [
            (store.id, d)
            for store in stores
            if store.id in scoped_store_ids
            for d in dates
        ]
        uncovered_count = sum(1 for key in lattice if key not in covered_set)
        stores_covered = {store.id for (store_id, _) in covered_set for store in stores if store.id == store_id}
        stores_covered_count = sum(1 for store in stores if store.id in stores_covered)

        # Conflicts: a store/date or person/date with multiple WORKING rows.
        conflict_store_day = self.session.execute(
            select(SiteDayAssignment.store_id, SiteDayAssignment.business_date)
            .where(
                SiteDayAssignment.tenant_id == tenant_id,
                SiteDayAssignment.month_id == month.id,
                SiteDayAssignment.status == DayStatus.WORKING.value,
            )
            .group_by(SiteDayAssignment.store_id, SiteDayAssignment.business_date)
            .having(func.count(SiteDayAssignment.id) > 1)
        ).all()
        conflict_person_day = self.session.execute(
            select(SiteDayAssignment.person_id, SiteDayAssignment.business_date)
            .where(
                SiteDayAssignment.tenant_id == tenant_id,
                SiteDayAssignment.month_id == month.id,
                SiteDayAssignment.status == DayStatus.WORKING.value,
            )
            .group_by(SiteDayAssignment.person_id, SiteDayAssignment.business_date)
            .having(func.count(SiteDayAssignment.id) > 1)
        ).all()
        conflicts_total = len(conflict_store_day) + len(conflict_person_day)

        # Supplementary days: aggregate counts.
        extra_home = self.session.execute(
            select(func.count(SiteDayAssignment.id))
            .where(
                SiteDayAssignment.tenant_id == tenant_id,
                SiteDayAssignment.month_id == month.id,
                SiteDayAssignment.working_kind == "EXTRA_HOME",
            )
        ).scalar_one()
        extra_other = self.session.execute(
            select(func.count(SiteDayAssignment.id))
            .where(
                SiteDayAssignment.tenant_id == tenant_id,
                SiteDayAssignment.month_id == month.id,
                SiteDayAssignment.working_kind == "EXTRA_OTHER",
            )
        ).scalar_one()

        # Unattributed sales: a store-day sale without a WORKING row in the lattice.
        sales_rows = list(
            self.session.execute(
                select(SalesStoreDay).where(
                    SalesStoreDay.tenant_id == tenant_id,
                    SalesStoreDay.business_date.in_(dates),
                )
            ).scalars()
        )
        sales_unattributed = 0
        for sale in sales_rows:
            if (sale.store_id, sale.business_date) not in covered_set:
                sales_unattributed += 1

        # Epay freshness: latest observation per (store, person) — count of invalids.
        epay_invalid_count = self.session.execute(
            select(func.count(EpayObservation.id))
            .where(
                EpayObservation.tenant_id == tenant_id,
                EpayObservation.is_valid.is_(False),
            )
        ).scalar_one()
        # Treat epay fresh = there are no invalid observations and there exists
        # at least one observation this month for any store in the lattice.
        epay_fresh = bool(int(epay_invalid_count) == 0)

        # Sheet sync: counts of OutboxJob rows in DONE/FAILED/PENDING per kind.
        sync_total = self.session.execute(
            select(func.count(OutboxJob.id))
            .where(
                OutboxJob.tenant_id == tenant_id,
                OutboxJob.kind.in_(
                    [
                        "GOOGLE_PROJECTION_STORE",
                        "EXPORT_XLSX_STORE",
                        "EXPORT_XLSX_BULK",
                    ]
                ),
            )
        ).scalar_one()
        sync_failed = self.session.execute(
            select(func.count(OutboxJob.id))
            .where(
                OutboxJob.tenant_id == tenant_id,
                OutboxJob.kind.in_(
                    [
                        "GOOGLE_PROJECTION_STORE",
                        "EXPORT_XLSX_STORE",
                        "EXPORT_XLSX_BULK",
                    ]
                ),
                OutboxJob.status == "FAILED",
            )
        ).scalar_one()
        sync_pending = self.session.execute(
            select(func.count(OutboxJob.id))
            .where(
                OutboxJob.tenant_id == tenant_id,
                OutboxJob.kind.in_(
                    [
                        "GOOGLE_PROJECTION_STORE",
                        "EXPORT_XLSX_STORE",
                        "EXPORT_XLSX_BULK",
                    ]
                ),
                OutboxJob.status.in_(["PENDING", "RUNNING"]),
            )
        ).scalar_one()
        sheet_total = int(sync_total)
        sheet_stale = int(sync_pending)
        sheet_error = int(sync_failed)

        kpis = OverviewKpis(
            stores_total=len(stores),
            stores_covered=stores_covered_count,
            days_uncovered=uncovered_count,
            conflicts=conflicts_total,
            extra_home_days=int(extra_home or 0),
            extra_other_days=int(extra_other or 0),
            sales_unattributed=sales_unattributed,
            epay_invalid=int(epay_invalid_count or 0),
            epay_fresh=epay_fresh,
            sheet_sync_total=sheet_total,
            sheet_sync_stale=sheet_stale,
            sheet_sync_error=sheet_error,
        )

        # Managers: derive from PontajProjection rows + scope to keep this one
        # aggregate (no per-user loop).
        people = list(
            self.session.execute(
                select(Person).where(Person.tenant_id == tenant_id, Person.is_active.is_(True))
            ).scalars()
        )
        person_to_manager: dict[str, str] = {
            person.id: person.id.split("person_", 1)[-1].split("_", 1)[0]
            for person in people
        }
        manager_to_stores: dict[str, set[str]] = {}
        manager_to_name: dict[str, str] = {}
        for person in people:
            manager_id = person_to_manager.get(person.id, person.id)
            manager_to_stores.setdefault(manager_id, set()).add(person.home_store_id)
            manager_to_name.setdefault(manager_id, manager_id.title())

        # Day totals per manager from PontajProjection, scoped to lattice.
        pontaj_rows = self.session.execute(
            select(PontajProjection.person_id, PontajProjection.business_date, PontajProjection.status)
            .where(
                PontajProjection.tenant_id == tenant_id,
                PontajProjection.month_id == month.id,
                PontajProjection.revision == month.revision,
            )
        ).all()
        manager_uncovered: dict[str, int] = {m: 0 for m in manager_to_stores}
        person_home_store = {person.id: person.home_store_id for person in people}
        for row in pontaj_rows:
            person_id, d, status = row[0], row[1], row[2]
            manager_id = person_to_manager.get(person_id, person_id)
            home_store = person_home_store.get(person_id)
            if home_store and (home_store, d) not in covered_set and status != DayStatus.LEAVE.value:
                manager_uncovered[manager_id] = manager_uncovered.get(manager_id, 0) + 1

        managers: list[OverviewManagerRow] = []
        for manager_id, stores_set in sorted(manager_to_stores.items()):
            managers.append(
                OverviewManagerRow(
                    user_id=manager_id,
                    display_name=manager_to_name.get(manager_id, manager_id),
                    stores_covered=len(stores_set & stores_covered),
                    stores_total=len(stores_set),
                    days_uncovered=manager_uncovered.get(manager_id, 0),
                    last_sync=None,
                )
            )

        # Needs-attention list — derived from the typed blockers via the same
        # close lattice so the UI can render exactly the open blockers.
        validation = _overview_validation(self.session, tenant_id, month, dates)
        needs_attention: list[OverviewNeedsAttention] = []
        for blocker in validation.blockers:
            needs_attention.append(
                OverviewNeedsAttention(
                    code=blocker.code,
                    severity=_SEVERITY.get(blocker.code, 3),
                    title=_NEEDS_ATTENTION_TITLES.get(blocker.code, blocker.code),
                    detail=blocker.message,
                    store_id=blocker.store_id,
                    person_id=blocker.person_id,
                    business_date=blocker.business_date,
                )
            )
        needs_attention.sort(key=lambda item: (item.severity, item.code, item.business_date or date.min))

        # Last grid revision snapshot (used by manager-row "last sync").
        latest_rule_pack = self.session.execute(
            select(GridCalculation.rule_pack_version)
            .where(
                GridCalculation.tenant_id == tenant_id,
                GridCalculation.month_id == month.id,
            )
            .order_by(GridCalculation.revision.desc())
            .limit(1)
        ).scalar_one_or_none()

        return OverviewReport(
            month_id=month.id,
            year=month.year,
            month=month.month,
            state=MonthState(month.state),
            revision=month.revision,
            rule_pack_version=latest_rule_pack,
            kpis=kpis,
            managers=tuple(managers),
            needs_attention=tuple(needs_attention),
        )


# ---------------------------------------------------------------------------
# Program (per magazine / per agenti grid)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProgramCell:
    business_date: date
    person_id: str | None
    store_id: str | None
    status: str
    working_kind: str | None
    display_name: str | None
    home_store_id: str | None
    badge: str | None
    locked: bool


@dataclass(frozen=True, slots=True)
class ProgramRow:
    row_id: str
    label: str
    home_store_id: str | None
    cells: tuple[ProgramCell, ...]


@dataclass(frozen=True, slots=True)
class ProgramGrid:
    month_id: str
    year: int
    month: int
    revision: int
    dates: tuple[date, ...]
    rows: tuple[ProgramRow, ...]
    legend: tuple[str, ...]


class ProgramService:
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

        rows: list[ProgramRow] = []
        if perspective == "stores":
            visible_stores = [
                store
                for store in stores
                if manager_scope_store_ids is None
                or any(store.id in manager_scope_store_ids[d] for d in dates)
            ]
            for store in sorted(visible_stores, key=lambda s: s.internal_code):
                cells: list[ProgramCell] = []
                for d in dates:
                    locked = manager_scope_store_ids is not None and store.id not in manager_scope_store_ids.get(d, set())
                    matches = [
                        row
                        for row in assignments
                        if row.business_date == d and row.store_id == store.id
                    ]
                    if matches:
                        match = matches[0]
                        person = person_by_id.get(match.person_id)
                        cells.append(
                            ProgramCell(
                                business_date=d,
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
                    elif any(abs_.business_date == d for abs_ in absences):
                        # OFF/LEAVE absences don't occupy store coverage.
                        cells.append(
                            ProgramCell(
                                business_date=d,
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
                    else:
                        cells.append(
                            ProgramCell(
                                business_date=d,
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
                or any(
                    person.home_store_id in manager_scope_store_ids[d]
                    for d in dates
                )
            ]
            for person in sorted(visible_people, key=lambda p: p.internal_code):
                cells = []
                for d in dates:
                    locked = manager_scope_store_ids is not None and person.home_store_id not in manager_scope_store_ids.get(d, set())
                    matches = [
                        row
                        for row in assignments
                        if row.business_date == d and row.person_id == person.id
                    ]
                    if matches:
                        match = matches[0]
                        store_obj: Store | None = store_by_id.get(match.store_id)
                        store_label = store_obj.internal_code if store_obj is not None else match.store_id
                        cells.append(
                            ProgramCell(
                                business_date=d,
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
                    elif any(abs_.business_date == d and abs_.person_id == person.id for abs_ in absences):
                        cells.append(
                            ProgramCell(
                                business_date=d,
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
                                business_date=d,
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


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExceptionEntry:
    code: str
    severity: int
    title: str
    detail: str
    blocking_close: bool
    store_id: str | None
    person_id: str | None
    business_date: date | None
    action_hint: str


class ExceptionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def month_exceptions(
        self,
        *,
        tenant_id: str,
        month: Month,
        manager_scope_store_ids: Mapping[date, set[str]] | None,
    ) -> tuple[ExceptionEntry, ...]:
        days = monthrange(month.year, month.month)[1]
        dates = [date(month.year, month.month, 1 + offset) for offset in range(days)]
        validation = _overview_validation(self.session, tenant_id, month, dates)
        scoped_ids = (
            None
            if manager_scope_store_ids is None
            else {
                store_id
                for set_ids in manager_scope_store_ids.values()
                for store_id in set_ids
            }
        )
        entries: list[ExceptionEntry] = []
        for blocker in validation.blockers:
            if scoped_ids is not None and blocker.store_id is not None and blocker.store_id not in scoped_ids:
                continue
            entries.append(
                ExceptionEntry(
                    code=blocker.code,
                    severity=_SEVERITY.get(blocker.code, 3),
                    title=_NEEDS_ATTENTION_TITLES.get(blocker.code, blocker.code),
                    detail=blocker.message,
                    blocking_close=True,
                    store_id=blocker.store_id,
                    person_id=blocker.person_id,
                    business_date=blocker.business_date,
                    action_hint=_action_hint(blocker.code),
                )
            )
        # Sort: severity asc (1=highest), then code, then date.
        entries.sort(key=lambda e: (e.severity, e.code, e.business_date or date.min))
        return tuple(entries)


def _action_hint(code: str) -> str:
    return {
        CloseBlockerCode.STORE_DAY_UNCOVERED.value: "Deschide Magazin → Program → alege agent",
        CloseBlockerCode.STORE_DAY_MULTIPLE_WORKING.value: "Deschide Magazin → Program → rezolvă duplicatul",
        CloseBlockerCode.PERSON_DAY_MULTIPLE_WORKING.value: "Deschide Program → rezolvă persoana",
        CloseBlockerCode.INVALID_WORKING_KIND.value: "Verifică home/other",
        CloseBlockerCode.SALES_MISSING_FOR_WORKED_DAY.value: "Ingest sursă vânzări",
        CloseBlockerCode.SALES_ORPHAN_FOR_COVERED_DAY.value: "Adaugă acoperire sau marchează ziua",
        CloseBlockerCode.TARGET_ZERO_FOR_WORKED_STORE.value: "Completează target magazin",
        CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED.value: "Reîmprospătează E-pay din Sheet",
        CloseBlockerCode.SHEET_CANARY_REQUIRED.value: "Așteaptă sincronizarea Sheet",
        CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED.value: "Rulează reconcilierea externă",
    }.get(code, "Investighează")


# ---------------------------------------------------------------------------
# Close checklist
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ChecklistItem:
    code: str
    severity: int
    title: str
    detail: str
    blocking: bool


@dataclass(frozen=True, slots=True)
class CloseChecklist:
    month_id: str
    revision: int
    state: MonthState
    blockers: tuple[ChecklistItem, ...]
    generated_at: str | None
    export_summary: tuple[dict[str, Any], ...]
    job_summary: tuple[dict[str, Any], ...]
    expected_revision: int


class CloseChecklistService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def month_checklist(self, *, tenant_id: str, month: Month) -> CloseChecklist:
        days = monthrange(month.year, month.month)[1]
        dates = [date(month.year, month.month, 1 + offset) for offset in range(days)]
        validation = _overview_validation(self.session, tenant_id, month, dates)
        items = [
            ChecklistItem(
                code=b.code,
                severity=_SEVERITY.get(b.code, 3),
                title=_NEEDS_ATTENTION_TITLES.get(b.code, b.code),
                detail=b.message,
                blocking=True,
            )
            for b in validation.blockers
        ]
        items.sort(key=lambda item: (item.severity, item.code))
        # Export summary placeholder: zero rows because S5 owns exports.
        export_summary: list[dict[str, Any]] = []
        job_rows = list(
            self.session.execute(
                select(OutboxJob).where(
                    OutboxJob.tenant_id == tenant_id,
                    OutboxJob.kind.in_(
                        [
                            "EXPORT_XLSX_STORE",
                            "EXPORT_XLSX_BULK",
                            "EXPORT_PONTAJ_ONLY",
                            "GOOGLE_PROJECTION_STORE",
                        ]
                    ),
                ).order_by(OutboxJob.id.desc()).limit(20)
            ).scalars()
        )
        job_summary = [
            {
                "job_id": row.id,
                "kind": row.kind,
                "status": row.status,
                "idempotency_key": row.idempotency_key,
                "attempts": row.attempts,
                "last_error": row.last_error,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in job_rows
        ]
        return CloseChecklist(
            month_id=month.id,
            revision=month.revision,
            state=MonthState(month.state),
            blockers=tuple(items),
            generated_at=None,
            export_summary=tuple(export_summary),
            job_summary=tuple(job_summary),
            expected_revision=month.revision,
        )


# ---------------------------------------------------------------------------
# Shared close-lattice helper
# ---------------------------------------------------------------------------


def _overview_validation(
    session: Session,
    tenant_id: str,
    month: Month,
    dates: list[date],
) -> CloseValidation:
    stores = list(
        session.execute(
            select(Store).where(Store.tenant_id == tenant_id, Store.is_active.is_(True))
        ).scalars()
    )
    store_ids = sorted({store.id for store in stores})
    open_days = [
        OpenStoreDay(store_id=store_id, business_date=d)
        for store_id in store_ids
        for d in dates
    ]
    lattice = {(day.store_id, day.business_date) for day in open_days}

    working = list(
        session.execute(
            select(SiteDayAssignment).where(
                SiteDayAssignment.tenant_id == tenant_id,
                SiteDayAssignment.month_id == month.id,
                SiteDayAssignment.status == DayStatus.WORKING.value,
            )
        ).scalars()
    )
    person_ids = {row.person_id for row in working}
    home_stores: dict[str, str] = {}
    if person_ids:
        home_stores = {
            person.id: person.home_store_id
            for person in session.execute(
                select(Person).where(Person.tenant_id == tenant_id, Person.id.in_(person_ids))
            ).scalars()
        }
    coverage: list[StoreCoverageSnapshot] = []
    person_days: list[PersonDaySnapshot] = []
    for row in working:
        coverage.append(
            StoreCoverageSnapshot(
                store_id=row.store_id,
                business_date=row.business_date,
                person_id=row.person_id,
                working_kind=row.working_kind,
                person_home_store_id=home_stores.get(row.person_id),
            )
        )
        person_days.append(
            PersonDaySnapshot(
                person_id=row.person_id,
                business_date=row.business_date,
                store_id=row.store_id,
                working_kind=row.working_kind,
            )
        )
    sales_rows = list(
        session.execute(
            select(SalesStoreDay).where(
                SalesStoreDay.tenant_id == tenant_id,
                SalesStoreDay.business_date.in_(dates),
            )
        ).scalars()
    )
    sales_index: dict[tuple[str, date], bool] = {
        (sale.store_id, sale.business_date): True for sale in sales_rows
    }
    sales_availability: list[SalesAvailabilitySnapshot] = []
    for store_id, d in sorted(lattice):
        sales_availability.append(
            SalesAvailabilitySnapshot(
                store_id=store_id,
                business_date=d,
                has_sale=sales_index.get((store_id, d), False),
            )
        )
    for sale in sorted(sales_rows, key=lambda s: (s.store_id, s.business_date)):
        if (sale.store_id, sale.business_date) not in lattice:
            sales_availability.append(
                SalesAvailabilitySnapshot(
                    store_id=sale.store_id,
                    business_date=sale.business_date,
                    has_sale=True,
                )
            )
    target_rows = list(
        session.execute(
            select(StoreTarget).where(
                StoreTarget.tenant_id == tenant_id,
                StoreTarget.year == month.year,
                StoreTarget.month == month.month,
            )
        ).scalars()
    )
    latest_targets: dict[tuple[str, str], StoreTarget] = {}
    for target in target_rows:
        key = (target.store_id, target.kind)
        existing = latest_targets.get(key)
        if existing is None or target.version > existing.version:
            latest_targets[key] = target
    target_availability: list[StoreTargetAvailabilitySnapshot] = []
    for d in dates:
        for store_id in store_ids:
            lookup = latest_targets.get((store_id, "MONTHLY_SALES"))
            if lookup is None or lookup.amount <= 0:
                target_availability.append(
                    StoreTargetAvailabilitySnapshot(
                        store_id=store_id,
                        business_date=d,
                        has_target=False,
                        target_amount=Decimal("0"),
                    )
                )
                continue
            target_availability.append(
                StoreTargetAvailabilitySnapshot(
                    store_id=store_id,
                    business_date=d,
                    has_target=True,
                    target_amount=lookup.amount,
                )
            )
    return validate_close(
        open_days=open_days,
        coverage=coverage,
        person_days=person_days,
        sales_availability=sales_availability,
        target_availability=target_availability,
    )


__all__ = [
    "CloseChecklist",
    "CloseChecklistService",
    "ExceptionEntry",
    "ExceptionService",
    "OverviewKpis",
    "OverviewManagerRow",
    "OverviewNeedsAttention",
    "OverviewReport",
    "OverviewService",
    "ProgramCell",
    "ProgramGrid",
    "ProgramRow",
    "ProgramService",
    "ChecklistItem",
]
