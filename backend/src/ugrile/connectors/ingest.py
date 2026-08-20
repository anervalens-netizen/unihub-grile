"""Fixture ingest service (AC-03).

Accepts a validated ``ConnectorV1Payload`` and applies it to PostgreSQL. The
ingest is idempotent for the tenant/generation pair: re-running produces the
same rows and never modifies audit state. Sales rows are upserted by
(tenant, store, date, generation) — the physical total is immutable; only
its presence is updated.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..domain.errors import ConnectorError, NotFoundError
from ..domain.identifiers import (
    make_person_id,
    make_store_id,
    make_tenant_id,
)
from ..repositories.models import Tenant
from .fixtures import FIXTURE_GENERATION, FIXTURE_TENANT_ID
from .v1_types import (
    ConnectorV1Payload,
    PersonRecord,
    SalesRecord,
    StoreRecord,
)


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

        tenant = self._ensure_tenant(tenant_id)

        stores_index = self._upsert_stores(payload.stores, tenant.id)
        people_index = self._upsert_people(payload.people, stores_index, tenant.id)
        sales_count = self._upsert_sales(payload.sales, stores_index, tenant.id, generation)

        self.session.flush()

        return {
            "stores": len(stores_index),
            "people": len(people_index),
            "sales": sales_count,
            "tenant": tenant.id,
            "generation": generation,
        }

    # --- helpers -----------------------------------------------------------

    def _ensure_tenant(self, tenant_id: str) -> Tenant:
        from ..repositories.models import Tenant

        existing = self.session.get(Tenant, tenant_id)
        if existing is not None:
            return existing
        # Display name is the bare tenant id without the ``tenant_`` prefix.
        bare = tenant_id[len("tenant_") :] if tenant_id.startswith("tenant_") else tenant_id
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
        self, records: list[StoreRecord], tenant_id: str
    ) -> dict[str, str]:
        from ..repositories.models import Store

        index: dict[str, str] = {}
        for record in records:
            # Records declare their tenant token (e.g. ``tenant_fixture``);
            # accept both the bare token and the prefixed id.
            record_tenant = record.tenant_id
            bare_tenant = (
                tenant_id[len("tenant_") :] if tenant_id.startswith("tenant_") else tenant_id
            )
            if record_tenant not in {tenant_id, bare_tenant}:
                raise ConnectorError(
                    "record tenant_id does not match payload tenant",
                    details={
                        "record_tenant_id": record_tenant,
                        "expected": [tenant_id, bare_tenant],
                    },
                )
            store_id = make_store_id(record.internal_code)
            row = self.session.get(Store, store_id)
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
            else:
                row.company_code = record.company_code
                row.external_code = record.external_code
                row.name = record.name
                row.is_active = record.is_active
            index[record.internal_code] = store_id
        self.session.flush()
        return index

    def _upsert_people(
        self,
        records: list[PersonRecord],
        stores_index: Mapping[str, str],
        tenant_id: str,
    ) -> dict[str, str]:
        from ..repositories.models import Person

        index: dict[str, str] = {}
        for record in records:
            home_store_id = stores_index.get(record.home_store_internal_code)
            if home_store_id is None:
                raise ConnectorError(
                    "person home_store_internal_code not declared in stores",
                    details={
                        "person": record.internal_code,
                        "missing_store": record.home_store_internal_code,
                    },
                )
            person_id = make_person_id(record.internal_code)
            row = self.session.get(Person, person_id)
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
            else:
                row.external_code = record.external_code
                row.display_name = record.display_name
                row.home_store_id = home_store_id
                row.is_active = record.is_active
            index[record.internal_code] = person_id
        self.session.flush()
        return index

    def _upsert_sales(
        self,
        records: list[SalesRecord],
        stores_index: Mapping[str, str],
        tenant_id: str,
        generation: str,
    ) -> int:
        from ..repositories.models import SalesStoreDay

        count = 0
        for record in records:
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
                )
                self.session.add(row)
            else:
                existing.amount = record.amount
                existing.currency = record.currency
                existing.source_ref = record.source_ref
            count += 1
        return count


__all__ = ["FixtureConnector"]


# Compatibility re-export for callers that need both the connector and the
# canonical fixture used by S1 smoke tests.
def get_default_fixture() -> ConnectorV1Payload:
    from .fixtures import default_fixture

    return default_fixture()


def get_default_tenant_id() -> str:
    return make_tenant_id(FIXTURE_TENANT_ID)


def get_default_generation() -> str:
    return FIXTURE_GENERATION


__all__ += [
    "get_default_fixture",
    "get_default_tenant_id",
    "get_default_generation",
]


def utcnow() -> datetime:
    return datetime.now(tz=UTC)
