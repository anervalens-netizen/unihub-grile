"""INT-011 financial read/close paths follow only the accepted Retail head."""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from ugrile.connectors.retail_adapter import FixtureRetailAdapter
from ugrile.connectors.retail_contract_v1 import RetailGenerationV1, RetailSnapshotV1
from ugrile.connectors.retail_ingest import RetailSnapshotIngestService
from ugrile.domain.errors import ConflictError
from ugrile.domain.identifiers import make_month_id
from ugrile.repositories.models import Month, Person, SiteDayAssignment, Store
from ugrile.repositories.retail_generation import accepted_retail_generation
from ugrile.services.attribution import AttributionService
from ugrile.services.financial_inputs import financial_input_mismatch
from ugrile.services.payroll_grid import PayrollGridService


def _accept(session, snapshot: RetailSnapshotV1) -> dict[str, object]:
    result = RetailSnapshotIngestService(session).apply_snapshot(
        snapshot,
        expected_tenant_id=snapshot.tenant_id,
        expected_period=snapshot.period,
    )
    session.commit()
    return result


def _advance(
    snapshot: RetailSnapshotV1,
    *,
    empty_financial: bool = False,
) -> RetailSnapshotV1:
    payload = snapshot.model_dump(mode="python")
    generation = snapshot.generation
    payload["generation"] = RetailGenerationV1(
        sales_hash="fixture-sales-v2",
        sales_revision=generation.sales_revision + 1,
        campaign_revision=generation.campaign_revision + 1,
        cutoff_date=generation.cutoff_date,
        generated_at=generation.generated_at + timedelta(minutes=10),
    )
    if empty_financial:
        payload["sales_store_day"] = []
        payload["targets"] = []
        payload["incentives"] = []
    else:
        sales = list(snapshot.sales_store_day)
        sales[0] = sales[0].model_copy(update={"amount": Decimal("13100.00")})
        payload["sales_store_day"] = sales
    return RetailSnapshotV1.model_validate(payload)


def test_payroll_grid_auto_tracks_accepted_head_and_invalidates_old_grid(session) -> None:
    tenant_id = "tenant_retail"
    first = FixtureRetailAdapter().load_snapshot(tenant_id=tenant_id, period="2026-08")
    first_result = _accept(session, first)

    store = session.execute(
        select(Store).where(Store.tenant_id == tenant_id, Store.external_code == "BUC-C")
    ).scalar_one()
    person = session.execute(
        select(Person).where(Person.tenant_id == tenant_id, Person.external_code == "alice")
    ).scalar_one()
    month = Month(
        id=make_month_id("retail", 2026, 8),
        tenant_id=tenant_id,
        year=2026,
        month=8,
        state="OPEN",
        revision=1,
    )
    session.add(month)
    session.flush()
    session.add(
        SiteDayAssignment(
            tenant_id=tenant_id,
            month_id=month.id,
            store_id=store.id,
            person_id=person.id,
            business_date=date(2026, 8, 1),
            status="WORKING",
            working_kind="NORMAL",
            revision=1,
            source="TEST",
        )
    )
    session.commit()

    _, first_rows = PayrollGridService(session).compute_and_persist(
        tenant_id=tenant_id,
        month=month,
    )
    session.commit()
    first_person_row = next(row for row in first_rows if row.person_id == person.id)
    first_payload = json.loads(first_person_row.payload)
    assert first_payload["inputs"]["sales_generation"] == first_result["generation_key"]

    second = _advance(first)
    second_result = _accept(session, second)
    session.refresh(month)
    assert month.revision == 1
    assert second_result["attribution_rows"] == 1

    current_attribution = AttributionService(session).latest_attribution(
        tenant_id=tenant_id,
        month=month,
    )
    assert len(current_attribution) == 1
    assert current_attribution[0].generation == second_result["generation_key"]
    assert current_attribution[0].amount == Decimal("13100.00")

    mismatch = financial_input_mismatch(
        session,
        tenant_id=tenant_id,
        month=month,
        person=person,
        row=first_person_row,
    )
    assert mismatch is not None
    assert "accepted generation changed" in mismatch

    accepted = accepted_retail_generation(
        session,
        tenant_id=tenant_id,
        period="2026-08",
    )
    assert accepted is not None
    assert accepted.generation_key == second_result["generation_key"]

    _, second_rows = PayrollGridService(session).compute_and_persist(
        tenant_id=tenant_id,
        month=month,
    )
    session.commit()
    second_person_row = next(row for row in second_rows if row.person_id == person.id)
    second_payload = json.loads(second_person_row.payload)
    assert second_payload["inputs"]["sales_generation"] == second_result["generation_key"]
    assert second_person_row.inputs_hash != first_person_row.inputs_hash


def test_closed_month_rejects_even_empty_financial_head_advance(session) -> None:
    tenant_id = "tenant_retail"
    first = FixtureRetailAdapter().load_snapshot(tenant_id=tenant_id, period="2026-08")
    first_result = _accept(session, first)
    month = Month(
        id=make_month_id("retail", 2026, 8),
        tenant_id=tenant_id,
        year=2026,
        month=8,
        state="CLOSED",
        revision=7,
    )
    session.add(month)
    session.commit()

    empty_next = _advance(first, empty_financial=True)
    with pytest.raises(ConflictError) as rejected:
        RetailSnapshotIngestService(session).apply_snapshot(
            empty_next,
            expected_tenant_id=tenant_id,
            expected_period="2026-08",
        )
    assert rejected.value.details["code"] == "MONTH_CLOSED"
    session.rollback()

    accepted = accepted_retail_generation(
        session,
        tenant_id=tenant_id,
        period="2026-08",
    )
    assert accepted is not None
    assert accepted.generation_key == first_result["generation_key"]
