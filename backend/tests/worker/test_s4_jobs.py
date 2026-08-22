"""S4 worker tests — the export / projection job kinds.

These tests preserve the legacy registry/dispatch contract while the M2 worker
adds revision-bound execution semantics.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from ugrile.connectors.fixtures import FIXTURE_TENANT_ID
from ugrile.domain.enums import JobKind
from ugrile.worker.jobs import get_handler, known_kinds
from ugrile.worker.worker import WORKER_LOCKED_BY, enqueue, run_once


def test_registry_contains_s4_kinds():
    kinds = set(known_kinds())
    assert JobKind.EXPORT_XLSX_STORE.value in kinds
    assert JobKind.EXPORT_XLSX_BULK.value in kinds
    assert JobKind.EXPORT_PONTAJ_ONLY.value in kinds
    assert JobKind.GOOGLE_PROJECTION_STORE.value in kinds


def test_export_xlsx_store_handler_persists_export_run(session):
    from ugrile.repositories.models import ExportRun

    run = ExportRun(
        tenant_id=FIXTURE_TENANT_ID,
        kind=JobKind.EXPORT_XLSX_STORE.value,
        status="PENDING",
        summary="{}",
    )
    session.add(run)
    session.flush()
    enqueue(
        session,
        tenant_id=FIXTURE_TENANT_ID,
        kind=JobKind.EXPORT_XLSX_STORE.value,
        idempotency_key="xlsx-store-1",
        payload={
            "store_id": "store_x",
            "month_id": "month_x",
            "month_revision": 0,
            "data_revision": 0,
            "export_run_id": run.id,
        },
    )
    session.commit()
    row, result = run_once(locked_by=WORKER_LOCKED_BY)
    assert result is None
    assert row is not None
    assert row.status == "FAILED"
    assert "month does not exist" in (row.last_error or "")
    session.refresh(run)
    assert run.status == "FAILED"
    assert run.artifact_uri is None


def test_google_projection_store_handler_requires_month_id(session):
    enqueue(
        session,
        tenant_id=FIXTURE_TENANT_ID,
        kind=JobKind.GOOGLE_PROJECTION_STORE.value,
        idempotency_key="google-1",
        payload={"store_id": "store_x", "spreadsheet_id": "abc"},
    )
    session.commit()
    row, result = run_once(locked_by=WORKER_LOCKED_BY)
    assert row is not None
    assert result is None
    assert row.status == "FAILED"
    assert "missing month_id" in (row.last_error or "")


def test_idempotency_key_prevents_duplicate(session):
    enqueue(
        session,
        tenant_id=FIXTURE_TENANT_ID,
        kind=JobKind.NOOP.value,
        idempotency_key="dup-1",
    )
    session.commit()
    try:
        enqueue(
            session,
            tenant_id=FIXTURE_TENANT_ID,
            kind=JobKind.NOOP.value,
            idempotency_key="dup-1",
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        pytest.skip("SQLite duplicate constraint path differs; PostgreSQL proof is separate")
    row, result = run_once(locked_by=WORKER_LOCKED_BY)
    assert row is not None
    session.commit()
    again, _ = run_once(locked_by=WORKER_LOCKED_BY)
    assert again is None


def test_handler_dispatch_resolves_new_kinds():
    for kind in [
        JobKind.EXPORT_XLSX_STORE,
        JobKind.EXPORT_XLSX_BULK,
        JobKind.EXPORT_PONTAJ_ONLY,
        JobKind.GOOGLE_PROJECTION_STORE,
    ]:
        assert get_handler(kind.value) is not None
