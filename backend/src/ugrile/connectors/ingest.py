"""Fixture ingest service (AC-03).

Accepts a validated ``ConnectorV1Payload`` and applies it to PostgreSQL. The
ingest is idempotent for the tenant/generation pair: re-running produces the
same rows and never modifies audit state. Sales rows are upserted by
(tenant, store, date, generation).

Tenant safety
-------------

* Store and Person identifiers are derived from ``(tenant_token, code)`` so
  two tenants can never collide on the same primary key.
* Upsert lookups use ``(tenant_id, internal_code)`` rather than the primary
  key, so the connector does not need to know the synthetic id.
* Foreign references are validated against the caller tenant before any
  write, so a fixture with a cross-tenant store reference fails fast with a
  precise error.
* Financial periods touched by sales/targets/incentives are locked before any
  payload write. A CLOSED period rejects the whole ingest atomically.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..domain.enums import MonthState
from ..domain.errors import ConflictError, ConnectorError, NotFoundError
from ..domain.identifiers import (
    make_person_id,
    make_store_id,
    make_tenant_id,
    tenant_slug_from_tenant_id,
)
from ..repositories.models import (
    IncentiveInput,
    Month,
    Person,
    Store,
    StoreTarget,
    Tenant,
)
from .v1_types import (
    ConnectorV1Payload,
    IncentiveRecord,
    PersonRecord,
    SalesRecord,
    StoreRecord,
    TargetRecord,
)

_VALID_TARGET_KINDS = {"MONTHLY_SALES", "MONTHLY_UNITS", "MONTHLY_ATTACH"}


class FixtureConnector:
    """Apply a v1 fixture payload to the database.

    The connector is the only sanctioned entry point for ``SalesStoreDay``
    rows at S1. Real Retail ingestion is out of scope until Stage 7.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def apply(self, payload: ConnectorV1Payload) -> dict[str, object]:
        header = payload.header
        tenant_id = header.tenant_id
        generation = header.generation

        if not tenant_id or not tenant_id.startswith("tenant_"):
            raise ConnectorError(
                "fixture header must use a tenant_-prefixed id",
                details={"tenant_id": tenant_id},
            )

        # This must run before even catalog writes. The same Month row lock is
        # used by close/reopen and the other financial write gates, so ingest
        # cannot race a close into publishing a mixed historical snapshot.
        self._lock_writable_financial_periods(payload, tenant_id)

        tenant = self._ensure_tenant(tenant_id)
        tenant_token = tenant_slug_from_tenant_id(tenant.id)

        stores_index = self._upsert_stores(payload.stores, tenant.id, tenant_token)
        people_index = self._upsert_people(payload.people, stores_index, tenant.id, tenant_token)
        sales_count = self._upsert_sales(
            payload.sales, stores_index, tenant.id, tenant_token, generation
        )
        targets_count = self._upsert_targets(
            payload.targets, stores_index, tenant.id, tenant_token, generation
        )
        incentives_count = self._upsert_incentives(
            payload.incentives, people_index, tenant.id, tenant_token, generation
        )

        self.session.flush()

        return {
            "stores": len(stores_index),
            "people": len(people_index),
            "sales": sales_count,
            "targets": targets_count,
            "incentives": incentives_count,
            "tenant": tenant.id,
            "generation": generation,
        }

    # --- helpers -----------------------------------------------------------

    def _lock_writable_financial_periods(
        self,
        payload: ConnectorV1Payload,
        tenant_id: str,
    ) -> None:
        periods = {
            (record.business_date.year, record.business_date.month)
            for record in payload.sales
        }
        periods.update((record.year, record.month) for record in payload.targets)
        periods.update((record.year, record.month) for record in payload.incentives)
        if not periods:
            return

        period_predicates = [
            and_(Month.year == year, Month.month == month)
            for year, month in sorted(periods)
        ]
        months = list(
            self.session.execute(
                select(Month)
                .where(
                    Month.tenant_id == tenant_id,
                    or_(*period_predicates),
                )
                .order_by(Month.year, Month.month)
                .with_for_update()
            ).scalars()
        )
        closed = [month for month in months if month.state == MonthState.CLOSED.value]
        if closed:
            raise ConflictError(
                "fixture ingest touches a closed financial period",
                details={
                    "code": "MONTH_CLOSED",
                    "closed_month_ids": [month.id for month in closed],
                    "closed_periods": [
                        f"{month.year:04d}-{month.month:02d}" for month in closed
                    ],
                },
            )

    def _ensure_tenant(self, tenant_id: str) -> Tenant:
        existing = self.session.get(Tenant, tenant_id)
        if existing is not None:
            return existing
        # Display name is the bare tenant id without the ``tenant_`` prefix.
        bare = tenant_slug_from_tenant_id(tenant_id)
        tenant = Tenant(
            id=tenant_id,
            name=bare.replace("_", " ").title(),
            timezone="Europe/Bucharest",
            is_active=True,
        )
        self.session.add(tenant)
        self.session.flush()
        return tenant

    def _upsert_stores(
        self,
        records: list[StoreRecord],
        tenant_id: str,
        tenant_token: str,
    ) -> dict[str, str]:
        index: dict[str, str] = {}
        for record in records:
            self._check_record_tenant(record.tenant_id, tenant_id, tenant_token)
            store_id = make_store_id(tenant_token, record.internal_code)
            # Tenant-scoped lookup: query by (tenant_id, internal_code), not
            # by primary key, so a different tenant's row can never be
            # matched.
            row = self._find_store_by_code(tenant_id, record.internal_code)
            if row is None:
                row = Store(
                    id=store_id,
                    tenant_id=tenant_id,
                    company_code=record.company_code,
                    internal_code=record.internal_code,
                    external_code=record.external_code,
                    name=record.name,
                    is_active=record.is_active,
                )
                self.session.add(row)
                self.session.flush()
            else:
                if row.id != store_id:
                    # Defensive: the persisted row carries the wrong id
                    # shape. This indicates a migration regression or a
                    # pre-existing legacy row; surface it loudly.
                    raise ConnectorError(
                        "store id shape mismatch for tenant-scoped lookup",
                        details={
                            "expected": store_id,
                            "found": row.id,
                            "tenant_id": tenant_id,
                            "internal_code": record.internal_code,
                        },
                    )
                row.company_code = record.company_code
                row.external_code = record.external_code
                row.name = record.name
                row.is_active = record.is_active
            index[record.internal_code] = row.id
        self.session.flush()
        return index

    def _upsert_people(
        self,
        records: list[PersonRecord],
        stores_index: Mapping[str, str],
        tenant_id: str,
        tenant_token: str,
    ) -> dict[str, str]:
        index: dict[str, str] = {}
        for record in records:
            self._check_record_tenant(record.tenant_id, tenant_id, tenant_token)
            home_store_id = stores_index.get(record.home_store_internal_code)
            if home_store_id is None:
                raise ConnectorError(
                    "person home_store_internal_code not declared in stores",
                    details={
                        "person": record.internal_code,
                        "missing_store": record.home_store_internal_code,
                    },
                )
            person_id = make_person_id(tenant_token, record.internal_code)
            row = self._find_person_by_code(tenant_id, record.internal_code)
            if row is None:
                row = Person(
                    id=person_id,
                    tenant_id=tenant_id,
                    internal_code=record.internal_code,
                    external_code=record.external_code,
                    display_name=record.display_name,
                    home_store_id=home_store_id,
                    is_active=record.is_active,
                )
                self.session.add(row)
                self.session.flush()
            else:
                if row.id != person_id:
                    raise ConnectorError(
                        "person id shape mismatch for tenant-scoped lookup",
                        details={
                            "expected": person_id,
                            "found": row.id,
                            "tenant_id": tenant_id,
                            "internal_code": record.internal_code,
                        },
                    )
                row.external_code = record.external_code
                row.display_name = record.display_name
                row.home_store_id = home_store_id
                row.is_active = record.is_active
            index[record.internal_code] = row.id
        self.session.flush()
        return index

    def _upsert_sales(
        self,
        records: list[SalesRecord],
        stores_index: Mapping[str, str],
        tenant_id: str,
        tenant_token: str,
        generation: str,
    ) -> int:
        from ..repositories.models import SalesStoreDay

        count = 0
        for record in records:
            self._check_record_tenant(record.tenant_id, tenant_id, tenant_token)
            store_id = stores_index.get(record.store_internal_code)
            if store_id is None:
                raise NotFoundError(
                    "sales record references unknown store",
                    details={"store_internal_code": record.store_internal_code},
                )
            existing = (
                self.session.query(SalesStoreDay)
                .filter_by(
                    tenant_id=tenant_id,
                    store_id=store_id,
                    business_date=record.business_date,
                    generation=generation,
                )
                .one_or_none()
            )
            if existing is None:
                row = SalesStoreDay(
                    tenant_id=tenant_id,
                    store_id=store_id,
                    business_date=record.business_date,
                    generation=generation,
                    amount=record.amount,
                    currency=record.currency,
                    source_ref=record.source_ref,
                    sim_quantity=record.sim_quantity,
                )
                self.session.add(row)
            else:
                existing.amount = record.amount
                existing.currency = record.currency
                existing.source_ref = record.source_ref
                existing.sim_quantity = record.sim_quantity
            count += 1
        return count

    def _upsert_targets(
        self,
        records: list[TargetRecord],
        stores_index: Mapping[str, str],
        tenant_id: str,
        tenant_token: str,
        generation: str,
    ) -> int:
        count = 0
        for record in records:
            self._check_record_tenant(record.tenant_id, tenant_id, tenant_token)
            if record.kind not in _VALID_TARGET_KINDS:
                raise ConnectorError(
                    "target kind is not recognised",
                    details={"kind": record.kind, "allowed": sorted(_VALID_TARGET_KINDS)},
                )
            store_id = stores_index.get(record.store_internal_code)
            if store_id is None:
                raise NotFoundError(
                    "target references unknown store",
                    details={"store_internal_code": record.store_internal_code},
                )
            existing = (
                self.session.query(StoreTarget)
                .filter_by(
                    tenant_id=tenant_id,
                    store_id=store_id,
                    year=record.year,
                    month=record.month,
                    kind=record.kind,
                    version=record.version,
                )
                .one_or_none()
            )
            if existing is None:
                row = StoreTarget(
                    tenant_id=tenant_id,
                    store_id=store_id,
                    year=record.year,
                    month=record.month,
                    kind=record.kind,
                    version=record.version,
                    amount=record.amount,
                    currency=record.currency,
                    sales_days=record.sales_days,
                )
                self.session.add(row)
            else:
                existing.amount = record.amount
                existing.currency = record.currency
                existing.sales_days = record.sales_days
            count += 1
        # ``generation`` is intentionally retained as part of the payload so
        # audit code can attribute the target without re-reading the row.
        _ = generation
        return count

    def _upsert_incentives(
        self,
        records: list[IncentiveRecord],
        people_index: Mapping[str, str],
        tenant_id: str,
        tenant_token: str,
        generation: str,
    ) -> int:
        count = 0
        for record in records:
            self._check_record_tenant(record.tenant_id, tenant_id, tenant_token)
            person_id = people_index.get(record.person_internal_code)
            if person_id is None:
                raise NotFoundError(
                    "incentive references unknown person",
                    details={"person_internal_code": record.person_internal_code},
                )
            existing = (
                self.session.query(IncentiveInput)
                .filter_by(
                    tenant_id=tenant_id,
                    person_id=person_id,
                    year=record.year,
                    month=record.month,
                    version=record.version,
                )
                .one_or_none()
            )
            if existing is None:
                row = IncentiveInput(
                    tenant_id=tenant_id,
                    person_id=person_id,
                    year=record.year,
                    month=record.month,
                    version=record.version,
                    amount=record.amount,
                    currency=record.currency,
                )
                self.session.add(row)
            else:
                existing.amount = record.amount
                existing.currency = record.currency
            count += 1
        _ = generation
        return count

    @staticmethod
    def _check_record_tenant(
        record_tenant: str, payload_tenant: str, payload_token: str
    ) -> None:
        """Reject cross-tenant records.

        Accept the canonical tenant id (``tenant_<token>``), the bare token,
        or the empty placeholder for sales/target records whose ``tenant_id``
        is optional metadata only.
        """

        if record_tenant in {"", payload_tenant, payload_token}:
            return
        raise ConnectorError(
            "record tenant_id does not match payload tenant",
            details={
                "record_tenant_id": record_tenant,
                "expected": [payload_tenant, payload_token],
            },
        )

    def _find_store_by_code(self, tenant_id: str, internal_code: str) -> Store | None:
        stmt = select(Store).where(
            Store.tenant_id == tenant_id, Store.internal_code == internal_code
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def _find_person_by_code(self, tenant_id: str, internal_code: str) -> Person | None:
        stmt = select(Person).where(
            Person.tenant_id == tenant_id, Person.internal_code == internal_code
        )
        return self.session.execute(stmt).scalar_one_or_none()


__all__ = ["FixtureConnector"]


# Compatibility re-export for callers that need both the connector and the
# canonical fixture used by S1 smoke tests.
def get_default_fixture() -> ConnectorV1Payload:
    from .fixtures import default_fixture

    return default_fixture()


def get_default_tenant_id() -> str:
    return make_tenant_id("fixture")


def get_default_generation() -> str:
    from .fixtures import FIXTURE_GENERATION

    return FIXTURE_GENERATION


__all__ += [
    "get_default_fixture",
    "get_default_tenant_id",
    "get_default_generation",
]


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _decimal_zero() -> Decimal:
    return Decimal("0")
