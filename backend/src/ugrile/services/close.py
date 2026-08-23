"""Month close / reopen service (AC-15 S3 slice).

The service owns the transactional state mutation and the audit chain. The
typed blocker detection lives in :mod:`ugrile.domain.close`; this module
turns the typed blockers into HTTP responses and appends the immutable
audit row.

Close contract
--------------

* Admin-only — non-admin callers get a typed ``FORBIDDEN`` response.
* The in-tenant ``Month`` row is acquired ``SELECT ... FOR UPDATE`` before any
  state/revision decision, so concurrent close/write attempts serialize.
* ``expected_revision`` (when provided) is validated against the locked row.
* Deterministic blocker detection runs over the full open-store/day lattice.
* A successful close appends exactly one audit event and sets state/revision in
  the same transaction.

Reopen contract
---------------

* Admin-only with a mandatory reason.
* The same in-tenant month row is locked before the CLOSED state check.
* The previous close remains append-only; reopen creates a new audit event.
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
    OpenStoreDay,
    PersonDaySnapshot,
    ReopenValidation,
    SalesAvailabilitySnapshot,
    StoreCoverageSnapshot,
    StoreTargetAvailabilitySnapshot,
    assert_close_state,
    assert_reopen_state,
    deferred_blockers,
    validate_close,
)
from ..domain.enums import CloseAction, MonthState
from ..domain.errors import NotFoundError, ScopeError, StaleRevisionError, ValidationError
from ..repositories.close import MonthCloseEventRepository
from ..repositories.models import (
    Month,
    SalesStoreDay,
    SiteDayAssignment,
    Store,
    StoreTarget,
)
from ..repositories.retail_generation import accepted_retail_generation
from .person_scope import effective_home_store_map


@dataclass(frozen=True, slots=True)
class CloseRequest:
    actor_id: str
    role_value: str
    expected_revision: int | None = None


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
        self.audit = MonthCloseEventRepository(session)

    def _lock_month(self, *, tenant_id: str, month_id: str) -> Month:
        """Lock only an in-tenant month row; foreign rows are never locked."""

        locked = self.session.execute(
            select(Month)
            .where(Month.id == month_id, Month.tenant_id == tenant_id)
            .with_for_update()
        ).scalar_one_or_none()
        if locked is None:
            raise NotFoundError(f"month not found: {month_id}")
        return locked

    def _build_snapshots(
        self, *, tenant_id: str, month: Month
    ) -> tuple[
        list[OpenStoreDay],
        list[StoreCoverageSnapshot],
        list[PersonDaySnapshot],
        list[SalesAvailabilitySnapshot],
        list[StoreTargetAvailabilitySnapshot],
    ]:
        days = monthrange(month.year, month.month)[1]
        dates = [date(month.year, month.month, 1 + offset) for offset in range(days)]
        stores = list(
            self.session.execute(
                select(Store).where(Store.tenant_id == tenant_id, Store.is_active.is_(True))
            ).scalars()
        )
        store_ids = sorted({store.id for store in stores})
        open_days = [
            OpenStoreDay(store_id=store_id, business_date=business_date)
            for store_id in store_ids
            for business_date in dates
        ]
        lattice = {(day.store_id, day.business_date) for day in open_days}

        working = list(
            self.session.execute(
                select(SiteDayAssignment).where(
                    SiteDayAssignment.tenant_id == tenant_id,
                    SiteDayAssignment.month_id == month.id,
                    SiteDayAssignment.status == "WORKING",
                )
            ).scalars()
        )
        effective_home = effective_home_store_map(
            self.session,
            tenant_id=tenant_id,
            person_ids={row.person_id for row in working},
            business_dates={row.business_date for row in working},
        )
        coverage: list[StoreCoverageSnapshot] = []
        person_days: list[PersonDaySnapshot] = []
        for row in working:
            home_store_id = effective_home.get((row.person_id, row.business_date))
            # Missing effective home history is financially ambiguous. Feed a
            # missing working kind into the typed validator so close fails with
            # INVALID_WORKING_KIND instead of silently trusting today's catalog.
            coverage.append(
                StoreCoverageSnapshot(
                    store_id=row.store_id,
                    business_date=row.business_date,
                    person_id=row.person_id,
                    working_kind=row.working_kind if home_store_id is not None else None,
                    person_home_store_id=home_store_id,
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

        period = f"{month.year:04d}-{month.month:02d}"
        accepted = accepted_retail_generation(
            self.session,
            tenant_id=tenant_id,
            period=period,
        )
        sales_stmt = select(SalesStoreDay).where(
            SalesStoreDay.tenant_id == tenant_id,
            SalesStoreDay.business_date.in_(dates),
        )
        if accepted is not None:
            sales_stmt = sales_stmt.where(
                SalesStoreDay.generation == accepted.generation_key
            )
        sales = list(self.session.execute(sales_stmt).scalars())
        sales_index: dict[tuple[str, date], bool] = {}
        for sale_row in sales:
            sales_index[(sale_row.store_id, sale_row.business_date)] = True
        sales_availability: list[SalesAvailabilitySnapshot] = []
        for store_id, business_date in sorted(lattice):
            sales_availability.append(
                SalesAvailabilitySnapshot(
                    store_id=store_id,
                    business_date=business_date,
                    has_sale=sales_index.get((store_id, business_date), False),
                )
            )
        for sale_row in sorted(sales, key=lambda sale: (sale.store_id, sale.business_date)):
            if (sale_row.store_id, sale_row.business_date) not in lattice:
                sales_availability.append(
                    SalesAvailabilitySnapshot(
                        store_id=sale_row.store_id,
                        business_date=sale_row.business_date,
                        has_sale=True,
                    )
                )

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
        authoritative_targets: dict[tuple[str, str], StoreTarget] = {}
        if accepted is not None:
            expected_versions = {
                (store_id, kind): version
                for store_id, kind, version in accepted.target_versions
            }
            for target_row in target_rows:
                key = (target_row.store_id, target_row.kind)
                if expected_versions.get(key) == target_row.version:
                    authoritative_targets[key] = target_row
        else:
            for target_row in target_rows:
                key = (target_row.store_id, target_row.kind)
                existing = authoritative_targets.get(key)
                if existing is None or target_row.version > existing.version:
                    authoritative_targets[key] = target_row
        for business_date in dates:
            for store_id in store_ids:
                target_lookup = authoritative_targets.get((store_id, "MONTHLY_SALES"))
                if target_lookup is None or target_lookup.amount <= 0:
                    target_availability.append(
                        StoreTargetAvailabilitySnapshot(
                            store_id=store_id,
                            business_date=business_date,
                            has_target=False,
                            target_amount=Decimal("0"),
                        )
                    )
                    continue
                target_availability.append(
                    StoreTargetAvailabilitySnapshot(
                        store_id=store_id,
                        business_date=business_date,
                        has_target=True,
                        target_amount=target_lookup.amount,
                    )
                )
        return open_days, coverage, person_days, sales_availability, target_availability

    def _validation(self, *, tenant_id: str, month: Month) -> CloseValidation:
        open_days, coverage, person_days, sales, targets = self._build_snapshots(
            tenant_id=tenant_id,
            month=month,
        )
        return validate_close(
            open_days=open_days,
            coverage=coverage,
            person_days=person_days,
            sales_availability=sales,
            target_availability=targets,
            extra_blockers=deferred_blockers(),
        )

    @staticmethod
    def _enforced_blockers(validation: CloseValidation) -> tuple[BlockerDetail, ...]:
        deferred = set(deferred_blockers())
        return tuple(blocker for blocker in validation.blockers if blocker.code not in deferred)

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
        locked = self._lock_month(tenant_id=tenant_id, month_id=month_id)
        assert_close_state(locked.state)
        if request.expected_revision is not None and locked.revision != request.expected_revision:
            raise StaleRevisionError(
                "stale close revision",
                details={
                    "code": "STALE_REVISION",
                    "expected": request.expected_revision,
                    "current": locked.revision,
                },
            )
        validation = self._validation(tenant_id=tenant_id, month=locked)
        enforced = self._enforced_blockers(validation)
        if enforced:
            raise ValidationError(
                "month has blocking conditions",
                details={
                    "code": "CLOSE_BLOCKED",
                    "month_id": locked.id,
                    "blockers": [
                        {
                            "code": blocker.code.value,
                            "store_id": blocker.store_id,
                            "person_id": blocker.person_id,
                            "business_date": (
                                blocker.business_date.isoformat()
                                if blocker.business_date
                                else None
                            ),
                            "message": blocker.message,
                        }
                        for blocker in enforced
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
            blockers=[
                {
                    "code": blocker.code.value,
                    "store_id": blocker.store_id,
                    "person_id": blocker.person_id,
                    "business_date": (
                        blocker.business_date.isoformat() if blocker.business_date else None
                    ),
                    "message": blocker.message,
                }
                for blocker in validation.blockers
            ],
        )
        return CloseOutcome(
            month_id=locked.id,
            revision=locked.revision,
            new_state=locked.state,
            audit_event_id=audit.id,
            validation=CloseValidation(blockers=enforced),
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
        locked = self._lock_month(tenant_id=tenant_id, month_id=month_id)
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


_ = BlockerDetail