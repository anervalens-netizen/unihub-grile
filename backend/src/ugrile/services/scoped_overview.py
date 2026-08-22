"""Manager-safe Overview/Exception read adapters.

The original S4 aggregate service was written tenant-wide and only scoped the
store lattice. These adapters preserve the admin read model while rebuilding
manager aggregates from resources that are provably inside the effective
store scope for the relevant business date.
"""

from __future__ import annotations

import json
from calendar import monthrange
from collections import Counter
from collections.abc import Mapping
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ugrile.domain.close import BlockerDetail
from ugrile.domain.enums import CloseBlockerCode, DayStatus, MonthState
from ugrile.repositories.models import (
    EpayObservation,
    GridCalculation,
    Month,
    OutboxJob,
    Person,
    PontajProjection,
    SalesStoreDay,
    SiteDayAssignment,
    Store,
)
from ugrile.services.overview import (
    ExceptionEntry,
    ExceptionService,
    OverviewKpis,
    OverviewManagerRow,
    OverviewNeedsAttention,
    OverviewReport,
    OverviewService,
    _NEEDS_ATTENTION_TITLES,
    _SEVERITY,
    _action_hint,
    _overview_validation,
)
from ugrile.services.person_scope import effective_home_store_map

_SYNC_KINDS = {
    "GOOGLE_PROJECTION_STORE",
    "EXPORT_XLSX_STORE",
    "EXPORT_XLSX_BULK",
}


def _allowed_by_date(
    *,
    dates: list[date],
    active_store_ids: set[str],
    manager_scope_store_ids: Mapping[date, set[str]],
) -> dict[date, set[str]]:
    return {
        business_date: set(manager_scope_store_ids.get(business_date, set()))
        & active_store_ids
        for business_date in dates
    }


def _job_resources(row: OutboxJob) -> tuple[str | None, set[str]]:
    try:
        payload = json.loads(row.payload or "{}")
    except json.JSONDecodeError:
        return None, set()
    if not isinstance(payload, dict):
        return None, set()

    raw_month_id = payload.get("month_id")
    month_id = raw_month_id if isinstance(raw_month_id, str) and raw_month_id else None
    store_ids: set[str] = set()
    raw_store_id = payload.get("store_id")
    if isinstance(raw_store_id, str) and raw_store_id:
        store_ids.add(raw_store_id)
    raw_store_ids = payload.get("store_ids")
    if isinstance(raw_store_ids, list):
        store_ids.update(value for value in raw_store_ids if isinstance(value, str) and value)
    return month_id, store_ids


def _manager_visible_job(row: OutboxJob, *, month_id: str, visible_store_ids: set[str]) -> bool:
    persisted_month_id, store_ids = _job_resources(row)
    return (
        persisted_month_id == month_id
        and bool(store_ids)
        and store_ids.issubset(visible_store_ids)
    )


def _manager_visible_blocker(
    blocker: BlockerDetail,
    *,
    allowed_by_date: Mapping[date, set[str]],
    visible_store_ids: set[str],
    visible_person_ids: set[str],
    effective_home: Mapping[tuple[str, date], str],
    working_stores_by_person_date: Mapping[tuple[str, date], set[str]],
) -> bool:
    business_date = blocker.business_date

    if blocker.code == CloseBlockerCode.PERSON_DAY_MULTIPLE_WORKING:
        if blocker.person_id is None or business_date is None:
            return False
        assigned = working_stores_by_person_date.get((blocker.person_id, business_date), set())
        allowed = allowed_by_date.get(business_date, set())
        return len(assigned) > 1 and assigned.issubset(allowed)

    if blocker.store_id is not None:
        if business_date is not None:
            if blocker.store_id not in allowed_by_date.get(business_date, set()):
                return False
            if blocker.person_id is not None:
                return effective_home.get((blocker.person_id, business_date)) in allowed_by_date.get(
                    business_date, set()
                )
            return True
        if blocker.store_id not in visible_store_ids:
            return False
        return blocker.person_id is None or blocker.person_id in visible_person_ids

    if blocker.person_id is not None:
        if business_date is not None:
            return effective_home.get((blocker.person_id, business_date)) in allowed_by_date.get(
                business_date, set()
            )
        return blocker.person_id in visible_person_ids

    # Resource-free close blockers are admin/global operational information.
    # Managers cannot close the month and should not receive tenant-wide details.
    return False


class ScopedOverviewService:
    """Use the legacy tenant-wide aggregate for admins and a fail-closed manager view."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._admin_service = OverviewService(session)

    def month_overview(
        self,
        *,
        tenant_id: str,
        month: Month,
        manager_scope_store_ids: Mapping[date, set[str]] | None,
    ) -> OverviewReport:
        if manager_scope_store_ids is None:
            return self._admin_service.month_overview(
                tenant_id=tenant_id,
                month=month,
                manager_scope_store_ids=None,
            )

        days = monthrange(month.year, month.month)[1]
        dates = [date(month.year, month.month, day) for day in range(1, days + 1)]
        stores = list(
            self.session.execute(
                select(Store).where(Store.tenant_id == tenant_id, Store.is_active.is_(True))
            ).scalars()
        )
        active_store_ids = {store.id for store in stores}
        allowed = _allowed_by_date(
            dates=dates,
            active_store_ids=active_store_ids,
            manager_scope_store_ids=manager_scope_store_ids,
        )
        visible_store_ids = {store_id for store_ids in allowed.values() for store_id in store_ids}
        lattice = {
            (store_id, business_date)
            for business_date in dates
            for store_id in allowed[business_date]
        }

        working = list(
            self.session.execute(
                select(SiteDayAssignment).where(
                    SiteDayAssignment.tenant_id == tenant_id,
                    SiteDayAssignment.month_id == month.id,
                    SiteDayAssignment.status == DayStatus.WORKING.value,
                )
            ).scalars()
        )
        visible_working = [
            row
            for row in working
            if row.store_id in allowed.get(row.business_date, set())
        ]
        covered_set = {(row.store_id, row.business_date) for row in visible_working}
        store_day_counts = Counter((row.store_id, row.business_date) for row in visible_working)
        person_day_counts = Counter((row.person_id, row.business_date) for row in visible_working)
        conflicts_total = sum(count > 1 for count in store_day_counts.values()) + sum(
            count > 1 for count in person_day_counts.values()
        )

        sales_rows = list(
            self.session.execute(
                select(SalesStoreDay).where(
                    SalesStoreDay.tenant_id == tenant_id,
                    SalesStoreDay.business_date.in_(dates),
                )
            ).scalars()
        )
        visible_sales = [
            row
            for row in sales_rows
            if row.store_id in allowed.get(row.business_date, set())
        ]
        sales_unattributed = sum(
            (row.store_id, row.business_date) not in covered_set for row in visible_sales
        )

        epay_invalid = 0
        if visible_store_ids:
            epay_invalid = len(
                self.session.execute(
                    select(EpayObservation).where(
                        EpayObservation.tenant_id == tenant_id,
                        EpayObservation.store_id.in_(visible_store_ids),
                        EpayObservation.is_valid.is_(False),
                    )
                )
                .scalars()
                .all()
            )

        sync_rows = list(
            self.session.execute(
                select(OutboxJob).where(
                    OutboxJob.tenant_id == tenant_id,
                    OutboxJob.kind.in_(_SYNC_KINDS),
                )
            ).scalars()
        )
        visible_jobs = [
            row
            for row in sync_rows
            if _manager_visible_job(
                row,
                month_id=month.id,
                visible_store_ids=visible_store_ids,
            )
        ]

        people = list(
            self.session.execute(
                select(Person).where(Person.tenant_id == tenant_id, Person.is_active.is_(True))
            ).scalars()
        )
        effective_home = effective_home_store_map(
            self.session,
            tenant_id=tenant_id,
            person_ids={person.id for person in people},
            business_dates=set(dates),
        )
        visible_people = [
            person
            for person in people
            if any(
                effective_home.get((person.id, business_date))
                in allowed.get(business_date, set())
                for business_date in dates
            )
        ]
        visible_person_ids = {person.id for person in visible_people}

        manager_to_stores: dict[str, set[str]] = {}
        manager_to_name: dict[str, str] = {}
        person_to_manager: dict[str, str] = {}
        for person in visible_people:
            manager_id = person.id.split("person_", 1)[-1].split("_", 1)[0]
            person_to_manager[person.id] = manager_id
            manager_to_name.setdefault(manager_id, manager_id.title())
            homes = {
                effective_home[(person.id, business_date)]
                for business_date in dates
                if (person.id, business_date) in effective_home
                and effective_home[(person.id, business_date)]
                in allowed.get(business_date, set())
            }
            manager_to_stores.setdefault(manager_id, set()).update(homes)

        pontaj_rows: list[tuple[str, date, str]] = []
        if visible_person_ids:
            pontaj_rows = list(
                self.session.execute(
                    select(
                        PontajProjection.person_id,
                        PontajProjection.business_date,
                        PontajProjection.status,
                    ).where(
                        PontajProjection.tenant_id == tenant_id,
                        PontajProjection.month_id == month.id,
                        PontajProjection.revision == month.revision,
                        PontajProjection.person_id.in_(visible_person_ids),
                    )
                ).all()
            )
        manager_uncovered: dict[str, int] = {key: 0 for key in manager_to_stores}
        for person_id, business_date, status in pontaj_rows:
            home_store = effective_home.get((person_id, business_date))
            if home_store not in allowed.get(business_date, set()):
                continue
            if (home_store, business_date) in covered_set or status == DayStatus.LEAVE.value:
                continue
            manager_id = person_to_manager.get(person_id, person_id)
            manager_uncovered[manager_id] = manager_uncovered.get(manager_id, 0) + 1

        covered_store_ids = {store_id for store_id, _ in covered_set}
        managers = tuple(
            OverviewManagerRow(
                user_id=manager_id,
                display_name=manager_to_name.get(manager_id, manager_id),
                stores_covered=len(store_ids & covered_store_ids),
                stores_total=len(store_ids),
                days_uncovered=manager_uncovered.get(manager_id, 0),
                last_sync=None,
            )
            for manager_id, store_ids in sorted(manager_to_stores.items())
        )

        working_stores_by_person_date: dict[tuple[str, date], set[str]] = {}
        for row in working:
            working_stores_by_person_date.setdefault(
                (row.person_id, row.business_date), set()
            ).add(row.store_id)

        validation = _overview_validation(self.session, tenant_id, month, dates)
        needs_attention = [
            OverviewNeedsAttention(
                code=blocker.code,
                severity=_SEVERITY.get(blocker.code, 3),
                title=_NEEDS_ATTENTION_TITLES.get(blocker.code, blocker.code),
                detail=blocker.message,
                store_id=blocker.store_id,
                person_id=blocker.person_id,
                business_date=blocker.business_date,
            )
            for blocker in validation.blockers
            if _manager_visible_blocker(
                blocker,
                allowed_by_date=allowed,
                visible_store_ids=visible_store_ids,
                visible_person_ids=visible_person_ids,
                effective_home=effective_home,
                working_stores_by_person_date=working_stores_by_person_date,
            )
        ]
        needs_attention.sort(
            key=lambda item: (item.severity, item.code, item.business_date or date.min)
        )

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
            kpis=OverviewKpis(
                stores_total=len(visible_store_ids),
                stores_covered=len(covered_store_ids),
                days_uncovered=len(lattice - covered_set),
                conflicts=conflicts_total,
                extra_home_days=sum(row.working_kind == "EXTRA_HOME" for row in visible_working),
                extra_other_days=sum(row.working_kind == "EXTRA_OTHER" for row in visible_working),
                sales_unattributed=sales_unattributed,
                epay_invalid=epay_invalid,
                epay_fresh=epay_invalid == 0,
                sheet_sync_total=len(visible_jobs),
                sheet_sync_stale=sum(row.status in {"PENDING", "RUNNING"} for row in visible_jobs),
                sheet_sync_error=sum(row.status == "FAILED" for row in visible_jobs),
            ),
            managers=managers,
            needs_attention=tuple(needs_attention),
        )


class ScopedExceptionService:
    """Fail closed for manager blocker rows that cannot be tied to visible resources."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._legacy = ExceptionService(session)

    def month_exceptions(
        self,
        *,
        tenant_id: str,
        month: Month,
        manager_scope_store_ids: Mapping[date, set[str]] | None,
    ) -> tuple[ExceptionEntry, ...]:
        if manager_scope_store_ids is None:
            return self._legacy.month_exceptions(
                tenant_id=tenant_id,
                month=month,
                manager_scope_store_ids=None,
            )

        days = monthrange(month.year, month.month)[1]
        dates = [date(month.year, month.month, day) for day in range(1, days + 1)]
        active_store_ids = set(
            self.session.execute(
                select(Store.id).where(Store.tenant_id == tenant_id, Store.is_active.is_(True))
            ).scalars()
        )
        allowed = _allowed_by_date(
            dates=dates,
            active_store_ids=active_store_ids,
            manager_scope_store_ids=manager_scope_store_ids,
        )
        visible_store_ids = {store_id for store_ids in allowed.values() for store_id in store_ids}

        working = list(
            self.session.execute(
                select(SiteDayAssignment).where(
                    SiteDayAssignment.tenant_id == tenant_id,
                    SiteDayAssignment.month_id == month.id,
                    SiteDayAssignment.status == DayStatus.WORKING.value,
                )
            ).scalars()
        )
        person_ids = {row.person_id for row in working}
        effective_home = effective_home_store_map(
            self.session,
            tenant_id=tenant_id,
            person_ids=person_ids,
            business_dates=set(dates),
        )
        visible_person_ids = {
            person_id
            for person_id in person_ids
            if any(
                effective_home.get((person_id, business_date))
                in allowed.get(business_date, set())
                for business_date in dates
            )
        }
        working_stores_by_person_date: dict[tuple[str, date], set[str]] = {}
        for row in working:
            working_stores_by_person_date.setdefault(
                (row.person_id, row.business_date), set()
            ).add(row.store_id)

        validation = _overview_validation(self.session, tenant_id, month, dates)
        entries = [
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
            for blocker in validation.blockers
            if _manager_visible_blocker(
                blocker,
                allowed_by_date=allowed,
                visible_store_ids=visible_store_ids,
                visible_person_ids=visible_person_ids,
                effective_home=effective_home,
                working_stores_by_person_date=working_stores_by_person_date,
            )
        ]
        entries.sort(key=lambda entry: (entry.severity, entry.code, entry.business_date or date.min))
        return tuple(entries)
