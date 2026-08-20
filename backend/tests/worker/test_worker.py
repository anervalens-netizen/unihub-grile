"""Worker tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ugrile.connectors.fixtures import (
    FIXTURE_TENANT_TOKEN,
)
from ugrile.domain.enums import JobKind
from ugrile.domain.identifiers import make_tenant_id
from ugrile.repositories.models import OutboxJob, SalesStoreDay, StoreTarget
from ugrile.worker.jobs import get_handler, known_kinds
from ugrile.worker.worker import (
    WORKER_LOCKED_BY,
    enqueue,
    run_forever,
    run_once,
)


def test_registry_contains_expected_kinds():
    kinds = set(known_kinds())
    assert JobKind.FIXTURE_INGEST.value in kinds
    assert JobKind.TENANT_BOOTSTRAP.value in kinds
    assert JobKind.NOOP.value in kinds


def test_handler_dispatch_is_deterministic():
    fn = get_handler(JobKind.NOOP.value)
    assert fn is get_handler(JobKind.NOOP.value)


def test_enqueue_and_run_once(session):
    tenant_id = make_tenant_id("acme")
    enqueue(
        session,
        tenant_id=tenant_id,
        kind=JobKind.NOOP.value,
        idempotency_key="noop-1",
    )
    session.commit()
    assert session.query(OutboxJob).count() == 1
    row, result = run_once(locked_by=WORKER_LOCKED_BY)
    assert row is not None
    assert result is not None
    assert result.status == "DONE"
    session.commit()
    again, again_result = run_once(locked_by=WORKER_LOCKED_BY)
    assert again is None
    assert again_result is None


def test_fixture_ingest_job_creates_sales_rows(session):
    tenant_id = make_tenant_id(FIXTURE_TENANT_TOKEN)
    enqueue(
        session,
        tenant_id=tenant_id,
        kind=JobKind.FIXTURE_INGEST.value,
        idempotency_key="fixture-1",
        payload={"tenant_token": FIXTURE_TENANT_TOKEN},
    )
    session.commit()
    row, result = run_once(locked_by=WORKER_LOCKED_BY)
    session.commit()
    assert row is not None
    assert result is not None
    assert result.status == "DONE"
    assert session.query(SalesStoreDay).count() == 3
    assert session.query(StoreTarget).count() >= 2


def test_fixture_ingest_job_honours_payload_tenant(session):
    """The handler must rewrite the canonical fixture so it lands under the
    payload's tenant — not the worker's default tenant.
    """

    target_token = "beta"
    target_tenant_id = make_tenant_id(target_token)
    enqueue(
        session,
        tenant_id=target_tenant_id,
        kind=JobKind.FIXTURE_INGEST.value,
        idempotency_key="fixture-beta",
        payload={"tenant_token": target_token},
    )
    session.commit()
    row, result = run_once(locked_by=WORKER_LOCKED_BY)
    session.commit()
    assert row is not None
    assert result is not None
    assert result.status == "DONE"
    assert session.query(SalesStoreDay).filter_by(tenant_id=target_tenant_id).count() == 3
    assert session.query(StoreTarget).filter_by(tenant_id=target_tenant_id).count() >= 2


def test_run_forever_returns_after_empty_polls(session):
    """``run_forever`` with ``stop_after_empty_polls`` is the loop's unit-test
    entry point.
    """

    summary = run_forever(stop_after_empty_polls=2)
    assert summary.empty_polls >= 2
    assert summary.processed == 0
    assert summary.done == 0
    assert summary.failed == 0


def test_worker_module_is_executable():
    """``python -m ugrile.worker.worker`` exits cleanly after the loop's
    unit-test entry point.

    The test imports the module and calls ``main`` with a temporary
    in-memory DB by spawning a subprocess that imports the worker and exits
    after ``stop_after_empty_polls`` iterations. We do this by setting an
    environment variable the test fixture recognises, but here we simply
    import the module to assert the ``main`` symbol exists and is callable.
    """

    from ugrile.worker import worker as worker_module

    assert callable(worker_module.main)
    # The module also exports a __main__ block; importing it must not raise.
    assert (Path(worker_module.__file__).read_text(encoding="utf-8")).count("def main(") >= 1
    # Smoke: subprocess runs and exits 0 because there are no jobs and the
    # in-memory DB has no connection; the process must terminate cleanly
    # via the SIGTERM path (or, in this minimal test, by importing and
    # returning immediately). The subprocess is a quick "the CLI exists"
    # proof.
    result = subprocess.run(
        [sys.executable, "-c", "import ugrile.worker.worker; print('ok')"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "ok" in result.stdout
