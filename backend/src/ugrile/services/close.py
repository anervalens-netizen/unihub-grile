"""Month close / reopen service (AC-15 S3 slice).

The service owns the transactional state mutation and the audit chain. The
typed blocker detection lives in :mod:`ugrile.domain.close`; this module
turns the typed blockers into HTTP responses and appends the immutable
audit row.

Close contract
--------------

* Admin-only — non-admin callers get a typed ``FORBIDDEN`` response.
* Deterministic blocker detection over the current month state.
* Month row is updated atomically with the audit chain append.
* A successful close freezes all subsequent business writes (the existing
  ``CalendarService`` already raises ``MONTH_CLOSED`` on any change).

Reopen contract
---------------

* Admin-only — non-admin callers get a typed ``FORBIDDEN`` response.
* Reason required (>= 4 chars, non-empty after trim).
* ``months.revision`` is bumped; ``state`` flips ``CLOSED`` → ``REOPENED``.
* A new audit chain entry is appended; the previous close row stays.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.close import (
    BlockerDetail,
    CloseValidation,
    PersonDaySnapshot,
    ReopenValidation,
    SalesAvailabilitySnapshot,
    StoreCoverageSnapshot,
    StoreTargetAvailabilitySnapshot,
    assert_close_state,
    assert_reopen_state,
    validate_close,
)
from ..domain.enums import CloseAction, MonthState
from ..domain.errors import NotFoundError, ScopeError, ValidationError
from ..repositories.close import MonthCloseEventRepository
from ..repositories.models import (
    Month,
    SalesStoreDay,
    SiteDayAssignment,
    Store,
    StoreTarget,
)
from ..repositories.months import MonthRepository


@dataclass(frozen=True, slots=True)
class CloseRequest:
    actor_id: str
    role_value: str


@dataclass(frozen=True, slots=True)
class ReopenRequest:
    actor_id: str
    role_value: str
    reason: str


@dataclass(frozen=True, slots=True)
class CloseOutcome:
    month_id: str
    revision: int
    new_state: str
    audit_event_id: int
    validation: CloseValidation


class CloseService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.months = MonthRepository(session)
        self.audit = MonthCloseEventRepository(session)

    # --- validation builders ---------------------------------------------

    def _build_snapshots(
        self, *, tenant_id: str, month: Month
    ) -> tuple[
        list[StoreCoverageSnapshot],
        list[PersonDaySnapshot],
        list[SalesAvailabilitySnapshot],
        list[StoreTargetAvailabilitySnapshot],
    ]:
        days = monthrange(month.year, month.month)[1]
        dates = [
            date(month.year, month.month, 1 + offset)
            for offset in range(days)
        ]
        working = list(
            self.session.execute(
                select(SiteDayAssignment).where(
                    SiteDayAssignment.tenant_id == tenant_id,
                    SiteDayAssignment.month_id == month.id,
                    SiteDayAssignment.status == "WORKING",
                )
            ).scalars()
        )
        coverage: list[StoreCoverageSnapshot] = []
        person_days: list[PersonDaySnapshot] = []
        for row in working:
            coverage.append(
                StoreCoverageSnapshot(
                    store_id=row.store_id,
                    business_date=row.business_date,
                    person_id=row.person_id,
                    working_kind=row.working_kind,
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

        sales = list(
            self.session.execute(
                select(SalesStoreDay).where(
                    SalesStoreDay.tenant_id == tenant_id,
                    SalesStoreDay.business_date.in_(dates),
                )
            ).scalars()
        )
        sales_index: dict[tuple[str, date], bool] = {}
        for sale_row in sales:
            sales_index[(sale_row.store_id, sale_row.business_date)] = True
        sales_availability: list[SalesAvailabilitySnapshot] = []
        for (store_id, business_date), present in sorted(sales_index.items()):
            sales_availability.append(
                SalesAvailabilitySnapshot(
                    store_id=store_id, business_date=business_date, has_sale=present
                )
            )
        for row in working:
            if (row.store_id, row.business_date) in sales_index:
                continue
            sales_availability.append(
                SalesAvailabilitySnapshot(
                    store_id=row.store_id,
                    business_date=row.business_date,
                    has_sale=False,
                )
            )

        stores = list(
            self.session.execute(
                select(Store).where(Store.tenant_id == tenant_id, Store.is_active.is_(True))
            ).scalars()
        )
        store_ids = sorted({store.id for store in stores})
        target_availability: list[StoreTargetAvailabilitySnapshot] = []
        target_rows = list(
            self.session.execute(
                select(StoreTarget).where(
                    StoreTarget.tenant_id == tenant_id,
                    StoreTarget.year == month.year,
                    StoreTarget.month == month.month,
                )
            ).scalars()
        )
        latest_targets: dict[tuple[str, str], StoreTarget] = {}
        for target_row in target_rows:
            key = (target_row.store_id, target_row.kind)
            existing = latest_targets.get(key)
            if existing is None or target_row.version > existing.version:
                latest_targets[key] = target_row
        for d in dates:
            for store_id in store_ids:
                target_lookup: StoreTarget | None = latest_targets.get(
                    (store_id, "MONTHLY_SALES")
                )
                if target_lookup is None or target_lookup.amount <= 0:
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
                        target_amount=target_lookup.amount,
                    )
                )
        return coverage, person_days, sales_availability, target_availability

    def _validation(
        self, *, tenant_id: str, month: Month
    ) -> CloseValidation:
        coverage, person_days, sales, targets = self._build_snapshots(
            tenant_id=tenant_id, month=month
        )
        return validate_close(
            coverage=coverage,
            person_days=person_days,
            sales_availability=sales,
            target_availability=targets,
        )

    # --- public API -------------------------------------------------------

    def close_month(
        self,
        *,
        tenant_id: str,
        month_id: str,
        request: CloseRequest,
    ) -> CloseOutcome:
        if request.role_value != "ADMIN":
            raise ScopeError(
                "admin role required to close a month",
                details={"role": request.role_value, "actor_id": request.actor_id},
            )
        month = self.months.get(month_id)
        if month.tenant_id != tenant_id:
            raise NotFoundError("month not found")
        assert_close_state(month.state)
        locked = self.session.execute(
            select(Month).where(Month.id == month_id).with_for_update()
        ).scalar_one()
        assert_close_state(locked.state)
        validation = self._validation(tenant_id=tenant_id, month=locked)
        if not validation.ok:
            raise ValidationError(
                "month has blocking conditions",
                details={
                    "code": "CLOSE_BLOCKED",
                    "month_id": locked.id,
                    "blockers": [
                        {
                            "code": b.code.value,
                            "store_id": b.store_id,
                            "person_id": b.person_id,
                            "business_date": b.business_date.isoformat()
                            if b.business_date
                            else None,
                            "message": b.message,
                        }
                        for b in validation.blockers
                    ],
                },
            )
        previous_state = locked.state
        revision_before = locked.revision
        locked.state = MonthState.CLOSED.value
        locked.revision = revision_before + 1
        audit = self.audit.append(
            tenant_id=tenant_id,
            month_id=locked.id,
            action=CloseAction.CLOSE,
            previous_state=previous_state,
            new_state=locked.state,
            revision_before=revision_before,
            revision_after=locked.revision,
            actor_id=request.actor_id,
            reason=None,
            blockers=[],
        )
        return CloseOutcome(
            month_id=locked.id,
            revision=locked.revision,
            new_state=locked.state,
            audit_event_id=audit.id,
            validation=validation,
        )

    def reopen_month(
        self,
        *,
        tenant_id: str,
        month_id: str,
        request: ReopenRequest,
    ) -> CloseOutcome:
        if request.role_value != "ADMIN":
            raise ScopeError(
                "admin role required to reopen a month",
                details={"role": request.role_value, "actor_id": request.actor_id},
            )
        validation = ReopenValidation(reason_required=True)
        ok, error = validation.validate_reason(request.reason)
        if not ok:
            raise ValidationError(
                error or "invalid reopen reason",
                details={"code": "REOPEN_REASON_REQUIRED"},
            )
        month = self.months.get(month_id)
        if month.tenant_id != tenant_id:
            raise NotFoundError("month not found")
        assert_reopen_state(month.state)
        locked = self.session.execute(
            select(Month).where(Month.id == month_id).with_for_update()
        ).scalar_one()
        assert_reopen_state(locked.state)
        previous_state = locked.state
        revision_before = locked.revision
        locked.state = MonthState.REOPENED.value
        locked.revision = revision_before + 1
        audit = self.audit.append(
            tenant_id=tenant_id,
            month_id=locked.id,
            action=CloseAction.REOPEN,
            previous_state=previous_state,
            new_state=locked.state,
            revision_before=revision_before,
            revision_after=locked.revision,
            actor_id=request.actor_id,
            reason=request.reason.strip(),
            blockers=[],
        )
        report = CloseValidation(blockers=tuple())
        return CloseOutcome(
            month_id=locked.id,
            revision=locked.revision,
            new_state=locked.state,
            audit_event_id=audit.id,
            validation=report,
        )


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


__all__ = [
    "CloseOutcome",
    "CloseRequest",
    "CloseService",
    "ReopenRequest",
    "utcnow",
]


# Keep references visible for mypy / future imports.
_ = BlockerDetail
