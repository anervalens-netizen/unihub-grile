"""Canonical Retail snapshot identity must not depend on provider row ordering."""

from __future__ import annotations

from sqlalchemy import func, select

from ugrile.connectors.retail_adapter import FixtureRetailAdapter
from ugrile.connectors.retail_ingest import RetailSnapshotIngestService, snapshot_sha256
from ugrile.repositories.models import ImportRun
from ugrile.repositories.retail_generation import accepted_retail_generation, retail_import_kind


def test_equivalent_reordered_snapshot_is_exact_replay(session) -> None:
    first = FixtureRetailAdapter().load_snapshot(
        tenant_id="tenant_retail",
        period="2026-08",
    )
    reordered = first.model_copy(
        update={
            "stores": list(reversed(first.stores)),
            "people": list(reversed(first.people)),
            "sales_store_day": list(reversed(first.sales_store_day)),
            "targets": list(reversed(first.targets)),
            "incentives": list(reversed(first.incentives)),
        }
    )

    assert snapshot_sha256(first) == snapshot_sha256(reordered)
    service = RetailSnapshotIngestService(session)
    accepted = service.apply_snapshot(
        first,
        expected_tenant_id=first.tenant_id,
        expected_period=first.period,
    )
    session.commit()
    replay = service.apply_snapshot(
        reordered,
        expected_tenant_id=reordered.tenant_id,
        expected_period=reordered.period,
    )
    session.commit()

    assert accepted["status"] == "ACCEPTED"
    assert replay["status"] == "REPLAYED"
    assert replay["generation_key"] == accepted["generation_key"]
    ledger_count = session.execute(
        select(func.count(ImportRun.id)).where(
            ImportRun.tenant_id == first.tenant_id,
            ImportRun.kind == retail_import_kind(first.period),
            ImportRun.status == "DONE",
        )
    ).scalar_one()
    assert ledger_count == 1

    head = accepted_retail_generation(
        session,
        tenant_id=first.tenant_id,
        period=first.period,
    )
    assert head is not None
    assert len(head.store_ids) == len(first.stores)
    assert len(head.person_ids) == len(first.people)
