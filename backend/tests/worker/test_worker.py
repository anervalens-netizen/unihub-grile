"""Worker tests."""

from __future__ import annotations

from ugrile.domain.enums import JobKind
from ugrile.repositories.models import OutboxJob
from ugrile.worker.jobs import get_handler, known_kinds
from ugrile.worker.worker import enqueue, run_once


def test_registry_contains_expected_kinds():
    kinds = set(known_kinds())
    assert JobKind.FIXTURE_INGEST.value in kinds
    assert JobKind.TENANT_BOOTSTRAP.value in kinds
    assert JobKind.NOOP.value in kinds


def test_handler_dispatch_is_deterministic():
    fn = get_handler(JobKind.NOOP.value)
    assert fn is get_handler(JobKind.NOOP.value)


def test_enqueue_and_run_once(session):
    from ugrile.domain.identifiers import make_tenant_id

    tenant_id = make_tenant_id("acme")
    enqueue(
        session,
        tenant_id=tenant_id,
        kind=JobKind.NOOP.value,
        idempotency_key="noop-1",
    )
    session.commit()
    assert session.query(OutboxJob).count() == 1
    row, result = run_once(locked_by="ugrile-test")
    assert row is not None
    assert result is not None
    assert result.status == "DONE"
    session.commit()
    again, again_result = run_once(locked_by="ugrile-test")
    assert again is None
    assert again_result is None


def test_fixture_ingest_job_creates_sales_rows(session):
    from ugrile.connectors.fixtures import FIXTURE_TENANT_ID
    from ugrile.domain.identifiers import make_tenant_id
    from ugrile.repositories.models import SalesStoreDay

    tenant_id = make_tenant_id(FIXTURE_TENANT_ID)
    enqueue(
        session,
        tenant_id=tenant_id,
        kind=JobKind.FIXTURE_INGEST.value,
        idempotency_key="fixture-1",
    )
    session.commit()
    row, result = run_once(locked_by="ugrile-test")
    session.commit()
    assert row is not None
    assert result is not None
    assert result.status == "DONE"
    assert session.query(SalesStoreDay).count() == 3
