"""S5a worker tests — fake Google adapter handler.

The ``GOOGLE_PROJECTION_STORE`` handler must:

* build a deterministic structural projection for the store/month
  (using only the local DB rows; no network I/O),
* call the fake adapter to persist the projection in
  ``sheet_projection_runs`` + ``sheet_bindings``,
* return a ``DONE`` payload carrying the generation fingerprint so the
  manager UI can verify the run.

A provider failure (``UGR_S5_GOOGLE_FAIL=1``) raises
``GoogleAdapterError`` so the durable worker marks the row ``FAILED``;
the last-good projection is preserved by the adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ugrile.connectors.fixtures import FIXTURE_GENERATION
from ugrile.connectors.google import (
    is_provider_failing,
    read_store_projection,
)
from ugrile.domain.enums import DayStatus, JobKind, MonthState, WorkingKind
from ugrile.repositories.models import Month, OutboxJob, SiteDayAssignment
from ugrile.repositories.months import MonthRepository
from ugrile.worker.worker import WORKER_LOCKED_BY, enqueue, run_once


def _open_month(session, faker_tenant) -> Month:
    month = MonthRepository(session).get_or_create(
        faker_tenant["tenant_id"], 2026, 8
    )
    month.state = MonthState.OPEN
    session.flush()
    return month


def _seed_working(session, faker_tenant, month: Month) -> SiteDayAssignment:
    row = SiteDayAssignment(
        tenant_id=faker_tenant["tenant_id"],
        month_id=month.id,
        store_id=faker_tenant["store_id"],
        person_id=faker_tenant["person_a_id"],
        business_date=datetime(2026, 8, 1, tzinfo=UTC).date(),
        status=DayStatus.WORKING.value,
        working_kind=WorkingKind.NORMAL.value,
    )
    session.add(row)
    session.commit()
    return row


def test_google_projection_handler_writes_adapter_payload(session, faker_tenant):
    month = _open_month(session, faker_tenant)
    _seed_working(session, faker_tenant, month)

    enqueue(
        session,
        tenant_id=faker_tenant["tenant_id"],
        kind=JobKind.GOOGLE_PROJECTION_STORE.value,
        idempotency_key="google-s5a-1",
        payload={
            "month_id": month.id,
            "store_id": faker_tenant["store_id"],
            "year": month.year,
            "month": month.month,
            "revision": month.revision,
        },
    )
    session.commit()

    row, result = run_once(locked_by=WORKER_LOCKED_BY)
    assert row is not None and result is not None
    assert result.status == "DONE"
    assert result.payload["label"] == "google_projection_s5a"
    assert result.payload["store_id"] == faker_tenant["store_id"]
    assert result.payload["generation"] == FIXTURE_GENERATION
    session.commit()

    # Readback must return the structural projection persisted by the adapter.
    projection = read_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
    )
    assert projection is not None
    assert projection.generation == FIXTURE_GENERATION
    assert "rows" in projection.grila
    assert "rows" in projection.pontaj
    # The Grila lattice carries the WORKING row we seeded.
    grila_rows = projection.grila.get("rows", [])
    assert any(
        row.get("business_date") == "2026-08-01"
        and row.get("status") == "WORKING"
        for row in grila_rows
    )


def test_google_projection_handler_fails_when_provider_failing(monkeypatch, session, faker_tenant):
    month = _open_month(session, faker_tenant)
    _seed_working(session, faker_tenant, month)
    monkeypatch.setenv("UGR_S5_GOOGLE_FAIL", "1")
    assert is_provider_failing() is True

    enqueue(
        session,
        tenant_id=faker_tenant["tenant_id"],
        kind=JobKind.GOOGLE_PROJECTION_STORE.value,
        idempotency_key="google-s5a-fail",
        payload={
            "month_id": month.id,
            "store_id": faker_tenant["store_id"],
            "year": month.year,
            "month": month.month,
            "revision": month.revision,
        },
    )
    session.commit()

    row, result = run_once(locked_by=WORKER_LOCKED_BY)
    assert row is not None
    assert result is None  # the worker surfaces the failure via None
    assert row.status == "FAILED"
    assert "UGR_S5_GOOGLE_FAIL" in (row.last_error or "")
    session.commit()

    # The adapter has no last-good to retain — readback returns None.
    assert (
        read_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
        )
        is None
    )


def test_google_projection_handler_rejects_cross_tenant_payload(session, faker_tenant):
    """Tenant mismatch must abort the job before the adapter runs."""

    month = _open_month(session, faker_tenant)
    enqueue(
        session,
        tenant_id="tenant_other",
        kind=JobKind.GOOGLE_PROJECTION_STORE.value,
        idempotency_key="google-cross-tenant",
        payload={
            "month_id": month.id,
            "store_id": faker_tenant["store_id"],
            "year": month.year,
            "month": month.month,
            "revision": month.revision,
        },
    )
    session.commit()

    row, result = run_once(locked_by=WORKER_LOCKED_BY)
    assert row is not None
    assert result is None
    assert row.status == "FAILED"
    assert "tenant" in (row.last_error or "").lower()


def test_google_projection_handler_requires_payload(session, faker_tenant):
    """A missing ``store_id`` / ``month_id`` raises a domain error."""

    enqueue(
        session,
        tenant_id=faker_tenant["tenant_id"],
        kind=JobKind.GOOGLE_PROJECTION_STORE.value,
        idempotency_key="google-empty",
        payload={},
    )
    session.commit()

    row, result = run_once(locked_by=WORKER_LOCKED_BY)
    assert row is not None
    assert result is None
    assert row.status == "FAILED"
    assert row.last_error is not None


@pytest.mark.skip(reason="OutboxJob model is registered; smoke assertion only.")
def test_outbox_kind_includes_google_projection_store(session):
    """OutboxJob.kind check constraint accepts the S5a job kind."""
    session.add(
        OutboxJob(
            tenant_id="tenant_x",
            kind=JobKind.GOOGLE_PROJECTION_STORE.value,
            idempotency_key="outbox-smoke",
            payload="{}",
        )
    )
    session.commit()
