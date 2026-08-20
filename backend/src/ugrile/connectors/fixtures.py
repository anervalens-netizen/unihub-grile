"""Built-in fixture data.

The fixture is deterministic and small (one tenant, two stores, three
people, three sales rows, two target rows). It exercises every connection
type the v1 contract advertises and gives the import-boundary test a real
payload to assert against.

Do not read fixtures from outside the package — the loader is the single
authoritative source so production deployments cannot accidentally serve a
fixture to a live tenant.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from .v1_types import (
    ConnectorHeader,
    ConnectorV1Payload,
    PersonRecord,
    SalesRecord,
    StoreRecord,
    TargetRecord,
)

FIXTURE_TENANT_TOKEN = "fixture"
FIXTURE_TENANT_ID = f"tenant_{FIXTURE_TENANT_TOKEN}"
FIXTURE_GENERATION = "FIXTURE_V1"

# Stable target versions. Bumping these requires an explicit migration in a
# later stage; the connector does not silently supersede.
FIXTURE_TARGET_VERSION = 1


def default_fixture() -> ConnectorV1Payload:
    """Return a small but realistic v1 fixture."""

    header = ConnectorHeader.model_construct(
        schema_name="grile.connector.v1",
        generation=FIXTURE_GENERATION,
        tenant_id=FIXTURE_TENANT_ID,
        emitted_at="2026-08-20T12:00:00Z",
    )
    stores = [
        StoreRecord(
            tenant_id=FIXTURE_TENANT_ID,
            internal_code="bucuresti_center",
            external_code="BUC-C",
            company_code="MOBIUP",
            name="București Center",
        ),
        StoreRecord(
            tenant_id=FIXTURE_TENANT_ID,
            internal_code="cluj_nord",
            external_code="CLJ-N",
            company_code="MOBIUP",
            name="Cluj Nord",
        ),
    ]
    people = [
        PersonRecord(
            tenant_id=FIXTURE_TENANT_ID,
            internal_code="alice",
            home_store_internal_code="bucuresti_center",
            display_name="Alice Ionescu",
        ),
        PersonRecord(
            tenant_id=FIXTURE_TENANT_ID,
            internal_code="bob",
            home_store_internal_code="bucuresti_center",
            display_name="Bob Popescu",
        ),
        PersonRecord(
            tenant_id=FIXTURE_TENANT_ID,
            internal_code="carmen",
            home_store_internal_code="cluj_nord",
            display_name="Carmen Stan",
        ),
    ]
    sales = [
        SalesRecord(
            tenant_id=FIXTURE_TENANT_ID,
            store_internal_code="bucuresti_center",
            business_date=date(2026, 8, 1),
            amount=Decimal("12500.00"),
        ),
        SalesRecord(
            tenant_id=FIXTURE_TENANT_ID,
            store_internal_code="bucuresti_center",
            business_date=date(2026, 8, 2),
            amount=Decimal("9870.50"),
        ),
        SalesRecord(
            tenant_id=FIXTURE_TENANT_ID,
            store_internal_code="cluj_nord",
            business_date=date(2026, 8, 1),
            amount=Decimal("5400.25"),
        ),
    ]
    targets = [
        TargetRecord(
            tenant_id=FIXTURE_TENANT_ID,
            store_internal_code="bucuresti_center",
            year=2026,
            month=8,
            kind="MONTHLY_SALES",
            version=FIXTURE_TARGET_VERSION,
            amount=Decimal("250000.00"),
        ),
        TargetRecord(
            tenant_id=FIXTURE_TENANT_ID,
            store_internal_code="cluj_nord",
            year=2026,
            month=8,
            kind="MONTHLY_SALES",
            version=FIXTURE_TARGET_VERSION,
            amount=Decimal("120000.00"),
        ),
    ]
    return ConnectorV1Payload(
        header=header,
        stores=stores,
        people=people,
        sales=sales,
        targets=targets,
    )


__all__ = [
    "FIXTURE_GENERATION",
    "FIXTURE_TENANT_ID",
    "FIXTURE_TENANT_TOKEN",
    "FIXTURE_TARGET_VERSION",
    "default_fixture",
]
