"""INT-008..011 adapter, negotiation and accepted-generation proofs."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from ugrile.connectors.retail_adapter import (
    FixtureRetailAdapter,
    RetailSnapshotAdapter,
    parse_retail_snapshot,
)
from ugrile.connectors.retail_contract_v1 import RetailGenerationV1, RetailSnapshotV1
from ugrile.connectors.retail_ingest import RetailSnapshotIngestService
from ugrile.domain.errors import ConflictError, ConnectorError
from ugrile.domain.grid import GridAnomalyCode
from ugrile.domain.identifiers import make_month_id
from ugrile.domain.rule_pack import get_default_rule_pack
from ugrile.domain.close_policy import policy_for_rule_pack
from ugrile.repositories.attribution import store_sales_for_month
from ugrile.repositories.models import ImportRun, Month, Person, SiteDayAssignment, Store
from ugrile.repositories.retail_generation import (
    accepted_retail_generation,
    retail_import_kind,
)
from ugrile.services.grid import GridService


def _apply(
    session,
    snapshot: RetailSnapshotV1,
) -> dict[str, object]:
    return RetailSnapshotIngestService(session).apply_snapshot(
        snapshot,
        expected_tenant_id=snapshot.tenant_id,
        expected_period=snapshot.period,
    )


def _next_snapshot(
    previous: RetailSnapshotV1,
    *,
    targets: list[object] | None = None,
    incentives: list[object] | None = None,
) -> RetailSnapshotV1:
    payload = previous.model_dump(mode="python")
    generation = previous.generation
    payload["generation"] = RetailGenerationV1(
        sales_hash="fixture-sales-v2",
        sales_revision=generation.sales_revision + 1,
        campaign_revision=generation.campaign_revision + 1,
        cutoff_date=generation.cutoff_date,
        generated_at=generation.generated_at + timedelta(minutes=5),
    )
    sales = list(previous.sales_store_day)
    sales[0] = sales[0].model_copy(update={"amount": Decimal("13000.00")})
    payload["sales_store_day"] = sales
    if targets is not None:
        payload["targets"] = targets
    if incentives is not None:
        payload["incentives"] = incentives
    return RetailSnapshotV1.model_validate(payload)


def test_fixture_adapter_implements_shared_contract_and_explicit_zero_inputs() -> None:
    adapter = FixtureRetailAdapter()

    assert isinstance(adapter, RetailSnapshotAdapter)
    snapshot = adapter.load_snapshot(tenant_id="tenant_retail", period="2026-08")

    assert snapshot.tenant_id == "tenant_retail"
    assert len(snapshot.incentives) == len(snapshot.people)
    by_person = {row.external_person_id: row for row in snapshot.incentives}
    assert by_person["alice"].amount == Decimal("350.00")
    assert by_person["bob"].amount == Decimal("0")
    assert by_person["bob"].authority_status == "fixture-explicit-zero"


def test_schema_negotiation_and_requested_period_fail_closed(session) -> None:
    snapshot = FixtureRetailAdapter().load_snapshot(
        tenant_id="tenant_retail",
        period="2026-08",
    )
    raw = snapshot.model_dump(mode="json")
    raw["schema_version"] = "retail-grile.v99"

    with pytest.raises(ConnectorError) as unsupported:
        parse_retail_snapshot(raw)
    assert unsupported.value.details["code"] == "RETAIL_SCHEMA_UNSUPPORTED"

    with pytest.raises(ConnectorError) as mismatch:
        RetailSnapshotIngestService(session).apply_snapshot(
            snapshot,
            expected_tenant_id="tenant_retail",
            expected_period="2026-07",
        )
    assert mismatch.value.details["code"] == "RETAIL_PERIOD_MISMATCH"


def test_replay_is_idempotent_and_financial_versions_are_pinned(session) -> None:
    snapshot = FixtureRetailAdapter().load_snapshot(
        tenant_id="tenant_retail",
        period="2026-08",
    )

    accepted_result = _apply(session, snapshot)
    session.commit()
    replay_result = _apply(session, snapshot)
    session.commit()

    assert accepted_result["status"] == "ACCEPTED"
    assert replay_result["status"] == "REPLAYED"
    assert replay_result["generation_key"] == accepted_result["generation_key"]

    ledger_count = session.execute(
        select(func.count(ImportRun.id)).where(
            ImportRun.tenant_id == snapshot.tenant_id,
            ImportRun.kind == retail_import_kind(snapshot.period),
            ImportRun.status == "DONE",
        )
    ).scalar_one()
    assert ledger_count == 1

    accepted = accepted_retail_generation(
        session,
        tenant_id=snapshot.tenant_id,
        period=snapshot.period,
    )
    assert accepted is not None
    assert len(accepted.target_versions) == len(snapshot.targets)
    assert len(accepted.incentive_versions) == len(snapshot.incentives)
    assert {version for _, _, version in accepted.target_versions} == {1}
    assert {version for _, version in accepted.incentive_versions} == {1}


def test_new_head_never_falls_back_to_old_sales_target_or_incentive(session) -> None:
    first = FixtureRetailAdapter().load_snapshot(
        tenant_id="tenant_retail",
        period="2026-08",
    )
    first_result = _apply(session, first)
    session.commit()

    store = session.execute(
        select(Store).where(
            Store.tenant_id == first.tenant_id,
            Store.external_code == "BUC-C",
        )
    ).scalar_one()
    person = session.execute(
        select(Person).where(
            Person.tenant_id == first.tenant_id,
            Person.external_code == "alice",
        )
    ).scalar_one()
    month = Month(
        id=make_month_id("retail", 2026, 8),
        tenant_id=first.tenant_id,
        year=2026,
        month=8,
        state="OPEN",
        revision=1,
    )
    assignment = SiteDayAssignment(
        tenant_id=first.tenant_id,
        month_id=month.id,
        store_id=store.id,
        person_id=person.id,
        business_date=date(2026, 8, 1),
        status="WORKING",
        working_kind="NORMAL",
        revision=1,
        source="TEST",
    )
    session.add_all([month, assignment])
    session.commit()

    second = _next_snapshot(first, targets=[], incentives=[])
    second_result = _apply(session, second)
    session.commit()

    assert first_result["generation_key"] != second_result["generation_key"]
    accepted = accepted_retail_generation(
        session,
        tenant_id=first.tenant_id,
        period=first.period,
    )
    assert accepted is not None
    assert accepted.generation_key == second_result["generation_key"]
    assert accepted.target_versions == ()
    assert accepted.incentive_versions == ()

    sales = store_sales_for_month(
        session,
        tenant_id=first.tenant_id,
        year=2026,
        month=8,
    )
    assert len(sales) == len(second.sales_store_day)
    assert {sale.generation for sale in sales} == {accepted.generation_key}
    buc_aug_1 = next(
        sale
        for sale in sales
        if sale.store_id == store.id and sale.business_date == date(2026, 8, 1)
    )
    assert buc_aug_1.amount == Decimal("13000.00")

    grid = GridService(session)
    days, _, anomalies, target_sources = grid._calendar_days(
        tenant_id=first.tenant_id,
        month=month,
        person_id=person.id,
        home_store_id=person.home_store_id,
        sales_generation=accepted.generation_key,
    )
    assert len(days) == 1
    assert days[0].target_amount == Decimal("0")
    assert target_sources == []
    codes = {str(anomaly["code"]) for anomaly in anomalies}
    assert GridAnomalyCode.TARGET_INPUT_MISSING.value in codes
    assert GridAnomalyCode.TARGET_ZERO.value not in codes

    incentive, incentive_missing = grid._incentive_for_with_status(
        tenant_id=first.tenant_id,
        person_id=person.id,
        month=month,
    )
    assert incentive == Decimal("0")
    assert incentive_missing is True

    policy = policy_for_rule_pack(get_default_rule_pack())
    assert policy.grid_is_blocking(GridAnomalyCode.TARGET_INPUT_MISSING)
    assert policy.grid_is_blocking(GridAnomalyCode.INCENTIVE_INPUT_MISSING)


def test_grid_rejects_nonaccepted_sales_generation(session) -> None:
    snapshot = FixtureRetailAdapter().load_snapshot(
        tenant_id="tenant_retail",
        period="2026-08",
    )
    _apply(session, snapshot)
    session.commit()

    store = session.execute(
        select(Store).where(
            Store.tenant_id == snapshot.tenant_id,
            Store.external_code == "BUC-C",
        )
    ).scalar_one()
    person = session.execute(
        select(Person).where(
            Person.tenant_id == snapshot.tenant_id,
            Person.external_code == "alice",
        )
    ).scalar_one()
    month = Month(
        id=make_month_id("retail", 2026, 8),
        tenant_id=snapshot.tenant_id,
        year=2026,
        month=8,
        state="OPEN",
        revision=0,
    )
    session.add(month)
    session.commit()

    with pytest.raises(ConflictError) as rejected:
        GridService(session)._calendar_days(
            tenant_id=snapshot.tenant_id,
            month=month,
            person_id=person.id,
            home_store_id=store.id,
            sales_generation="FIXTURE_V1",
        )
    assert rejected.value.details["code"] == "RETAIL_GENERATION_NOT_ACCEPTED"


def test_stale_transition_preserves_last_good_head(session) -> None:
    first = FixtureRetailAdapter().load_snapshot(
        tenant_id="tenant_retail",
        period="2026-08",
    )
    _apply(session, first)
    session.commit()
    second = _next_snapshot(first)
    accepted_second = _apply(session, second)
    session.commit()

    with pytest.raises(ConflictError) as stale:
        _apply(session, first)
    assert stale.value.details["code"] == "RETAIL_GENERATION_STALE"
    session.rollback()

    accepted = accepted_retail_generation(
        session,
        tenant_id=first.tenant_id,
        period=first.period,
    )
    assert accepted is not None
    assert accepted.generation_key == accepted_second["generation_key"]
