"""Provider-neutral Retail snapshot adapter boundary (INT-008/INT-010).

Adapters only load and validate :class:`RetailSnapshotV1`. They do not mutate
Grile persistence. The built-in fixture adapter exercises the exact same DTO
contract a future Retail adapter must implement.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from pydantic import ValidationError as PydanticValidationError

from ..domain.errors import ConnectorError
from .fixtures import default_fixture
from .retail_contract_v1 import (
    RETAIL_GRILE_SCHEMA_V1,
    RetailGenerationV1,
    RetailIncentiveV1,
    RetailPersonV1,
    RetailSalesStoreDayV1,
    RetailSnapshotV1,
    RetailStoreV1,
    RetailTargetV1,
)


@runtime_checkable
class RetailSnapshotAdapter(Protocol):
    """Load one complete external-input snapshot without mutating Grile."""

    def load_snapshot(self, *, tenant_id: str, period: str) -> RetailSnapshotV1: ...


def parse_retail_snapshot(payload: Mapping[str, Any]) -> RetailSnapshotV1:
    """Negotiate the supported schema and fail closed on malformed input."""

    schema_version = payload.get("schema_version")
    if schema_version != RETAIL_GRILE_SCHEMA_V1:
        raise ConnectorError(
            "unsupported Retail integration schema",
            details={
                "code": "RETAIL_SCHEMA_UNSUPPORTED",
                "supported": [RETAIL_GRILE_SCHEMA_V1],
                "received": schema_version,
            },
        )
    try:
        return RetailSnapshotV1.model_validate(dict(payload))
    except PydanticValidationError as exc:
        raise ConnectorError(
            "Retail integration snapshot failed validation",
            details={
                "code": "RETAIL_SNAPSHOT_INVALID",
                "errors": exc.errors(include_url=False, include_input=False),
            },
        ) from exc


class FixtureRetailAdapter:
    """Deterministic adapter backed by the package fixture, not a separate path."""

    def load_snapshot(self, *, tenant_id: str, period: str) -> RetailSnapshotV1:
        if period != "2026-08":
            raise ConnectorError(
                "fixture Retail adapter only contains the canonical 2026-08 period",
                details={
                    "code": "RETAIL_FIXTURE_PERIOD_UNAVAILABLE",
                    "period": period,
                },
            )
        fixture = default_fixture()
        store_external_by_internal = {
            store.internal_code: (store.external_code or store.internal_code)
            for store in fixture.stores
        }
        stores = [
            RetailStoreV1(
                external_store_id=store_external_by_internal[store.internal_code],
                display_name=store.name,
                company_code=store.company_code,
                is_active=store.is_active,
            )
            for store in fixture.stores
        ]
        people = [
            RetailPersonV1(
                external_person_id=person.internal_code,
                display_name=person.display_name,
                home_store_external_id=store_external_by_internal[
                    person.home_store_internal_code
                ],
                is_active=person.is_active,
            )
            for person in fixture.people
        ]
        sales = [
            RetailSalesStoreDayV1(
                external_store_id=store_external_by_internal[sale.store_internal_code],
                business_date=sale.business_date,
                amount=sale.amount,
                currency=sale.currency,
                sim_quantity=sale.sim_quantity,
            )
            for sale in fixture.sales
        ]
        targets = [
            RetailTargetV1(
                external_store_id=store_external_by_internal[target.store_internal_code],
                year=target.year,
                month=target.month,
                kind=target.kind,
                amount=target.amount,
                currency=target.currency,
                sales_days=target.sales_days,
            )
            for target in fixture.targets
        ]
        incentive_by_person = {
            incentive.person_internal_code: incentive for incentive in fixture.incentives
        }
        # ``RetailSnapshotV1.complete=True`` means absence cannot carry an
        # implicit monetary zero. The fixture therefore emits an explicit row
        # for every person, including explicit zero/no-campaign observations.
        incentives: list[RetailIncentiveV1] = []
        for person in fixture.people:
            incentive = incentive_by_person.get(person.internal_code)
            incentives.append(
                RetailIncentiveV1(
                    external_person_id=person.internal_code,
                    year=2026,
                    month=8,
                    amount=(
                        incentive.amount if incentive is not None else Decimal("0")
                    ),
                    currency=incentive.currency if incentive is not None else "RON",
                    authority_status=(
                        "fixture" if incentive is not None else "fixture-explicit-zero"
                    ),
                )
            )
        cutoff = max(sale.business_date for sale in sales)
        snapshot = RetailSnapshotV1(
            tenant_id=tenant_id,
            timezone="Europe/Bucharest",
            period=period,
            generation=RetailGenerationV1(
                sales_hash="fixture-sales-v1",
                sales_revision=1,
                campaign_revision=1,
                cutoff_date=cutoff,
                generated_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
            ),
            stores=stores,
            people=people,
            sales_store_day=sales,
            targets=targets,
            incentives=incentives,
        )
        return snapshot


__all__ = [
    "FixtureRetailAdapter",
    "RetailSnapshotAdapter",
    "parse_retail_snapshot",
]
