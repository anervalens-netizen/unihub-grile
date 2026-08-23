"""INT-002..007 contract tests for the Grile-owned Retail DTO boundary."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from ugrile.connectors.retail_contract_v1 import (
    RETAIL_GRILE_SCHEMA_V1,
    RetailGenerationV1,
    RetailIncentiveV1,
    RetailManagerScopeV1,
    RetailPayrollInputV1,
    RetailPersonV1,
    RetailSalesStoreDayV1,
    RetailSnapshotV1,
    RetailStoreV1,
    RetailTargetV1,
)


def _generation(*, cutoff: date = date(2026, 8, 20)) -> RetailGenerationV1:
    return RetailGenerationV1(
        sales_hash="sales-head-abc",
        sales_revision=12,
        campaign_revision=7,
        cutoff_date=cutoff,
        generated_at=datetime(2026, 8, 20, 10, 30, tzinfo=UTC),
    )


def _snapshot(**updates: object) -> RetailSnapshotV1:
    payload: dict[str, object] = {
        "schema_version": RETAIL_GRILE_SCHEMA_V1,
        "tenant_id": "tenant_acme",
        "timezone": "Europe/Bucharest",
        "period": "2026-08",
        "generation": _generation(),
        "complete": True,
        "stores": [
            RetailStoreV1(
                external_store_id="S001",
                display_name="Magazin 1",
                company_code="MOBIUP",
            )
        ],
        "people": [
            RetailPersonV1(
                external_person_id="A001",
                display_name="Agent 1",
                home_store_external_id="S001",
            )
        ],
        "manager_scopes": [
            RetailManagerScopeV1(
                manager_key="manager-east",
                regional_key="regional-east",
                store_external_id="S001",
                valid_from_month="2026-06",
            )
        ],
        "sales_store_day": [
            RetailSalesStoreDayV1(
                external_store_id="S001",
                business_date=date(2026, 8, 20),
                amount=Decimal("1234.50"),
                sim_quantity=3,
            )
        ],
        "targets": [
            RetailTargetV1(
                external_store_id="S001",
                year=2026,
                month=8,
                kind="MONTHLY_SALES",
                amount=Decimal("20000"),
                sales_days=26,
            )
        ],
        "incentives": [
            RetailIncentiveV1(
                external_person_id="A001",
                year=2026,
                month=8,
                amount=Decimal("350"),
                authority_status="final",
            )
        ],
        "payroll_inputs": [
            RetailPayrollInputV1(
                external_person_id="A001",
                year=2026,
                month=8,
                input_kind="EXTERNAL_BONUS",
                amount=Decimal("25"),
            )
        ],
    }
    payload.update(updates)
    return RetailSnapshotV1.model_validate(payload)


def test_snapshot_accepts_complete_generation_pinned_contract() -> None:
    snapshot = _snapshot()

    assert snapshot.schema_version == RETAIL_GRILE_SCHEMA_V1
    assert snapshot.complete is True
    assert snapshot.generation.sales_revision == 12
    assert snapshot.generation.campaign_revision == 7
    assert snapshot.stores[0].external_store_id == "S001"
    assert snapshot.people[0].home_store_external_id == "S001"
    assert snapshot.manager_scopes[0].valid_to_month is None
    assert snapshot.sales_store_day[0].amount == Decimal("1234.50")
    assert snapshot.targets[0].sales_days == 26
    assert snapshot.incentives[0].amount == Decimal("350")


def test_snapshot_rejects_unsupported_schema_and_incomplete_payload() -> None:
    base = _snapshot().model_dump(mode="python")

    with pytest.raises(ValidationError):
        RetailSnapshotV1.model_validate({**base, "schema_version": "retail-grile.v2"})
    with pytest.raises(ValidationError):
        RetailSnapshotV1.model_validate({**base, "complete": False})


def test_snapshot_rejects_invalid_timezone_and_naive_generation_time() -> None:
    with pytest.raises(ValidationError):
        _snapshot(timezone="Bucharest-ish")

    with pytest.raises(ValidationError):
        RetailGenerationV1(
            sales_hash="x",
            sales_revision=1,
            campaign_revision=1,
            cutoff_date=date(2026, 8, 1),
            generated_at=datetime(2026, 8, 1, 12, 0),
        )


def test_snapshot_rejects_cross_period_or_post_cutoff_sales() -> None:
    with pytest.raises(ValidationError):
        _snapshot(generation=_generation(cutoff=date(2026, 7, 31)))

    with pytest.raises(ValidationError):
        _snapshot(
            sales_store_day=[
                RetailSalesStoreDayV1(
                    external_store_id="S001",
                    business_date=date(2026, 8, 21),
                    amount=Decimal("1"),
                )
            ]
        )


def test_snapshot_rejects_unknown_references_and_duplicate_business_identity() -> None:
    with pytest.raises(ValidationError):
        _snapshot(
            people=[
                RetailPersonV1(
                    external_person_id="A001",
                    home_store_external_id="MISSING",
                )
            ]
        )

    duplicate = RetailSalesStoreDayV1(
        external_store_id="S001",
        business_date=date(2026, 8, 20),
        amount=Decimal("1"),
    )
    with pytest.raises(ValidationError):
        _snapshot(sales_store_day=[duplicate, duplicate])


def test_effective_scope_interval_is_ordered_and_target_sales_days_may_be_missing() -> None:
    with pytest.raises(ValidationError):
        RetailManagerScopeV1(
            manager_key="manager",
            store_external_id="S001",
            valid_from_month="2026-08",
            valid_to_month="2026-07",
        )

    snapshot = _snapshot(
        targets=[
            RetailTargetV1(
                external_store_id="S001",
                year=2026,
                month=8,
                kind="MONTHLY_SALES",
                amount=Decimal("20000"),
                sales_days=None,
            )
        ]
    )
    # Missing authoritative selling-day metadata stays missing; it is never
    # normalized to a calendar-derived zero/default by the integration DTO.
    assert snapshot.targets[0].sales_days is None


def test_financial_inputs_reject_negative_authoritative_values_where_defined() -> None:
    with pytest.raises(ValidationError):
        RetailSalesStoreDayV1(
            external_store_id="S001",
            business_date=date(2026, 8, 1),
            amount=Decimal("-0.01"),
        )
    with pytest.raises(ValidationError):
        RetailTargetV1(
            external_store_id="S001",
            year=2026,
            month=8,
            kind="MONTHLY_SALES",
            amount=Decimal("-1"),
        )
    with pytest.raises(ValidationError):
        RetailIncentiveV1(
            external_person_id="A001",
            year=2026,
            month=8,
            amount=Decimal("-1"),
        )
