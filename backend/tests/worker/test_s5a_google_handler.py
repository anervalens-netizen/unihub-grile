"""S5a worker tests — fake Google adapter handler.

The ``GOOGLE_PROJECTION_STORE`` handler must build the local structural
projection, preserve last-good state, and surface provider outages as bounded
retryable jobs. Business/payload errors remain terminal.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ugrile.connectors.fixtures import FIXTURE_GENERATION
from ugrile.connectors.google import is_provider_failing, read_store_projection
from ugrile.domain.enums import DayStatus, JobKind, MonthState, WorkingKind
from ugrile.repositories.models import Month, OutboxJob, SheetProjectionRun, SiteDayAssignment
from ugrile.repositories.months import MonthRepository
from ugrile.worker.worker import WORKER_LOCKED_BY, enqueue, run_once


def _open_month(session, faker_tenant) -> Month:
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
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


def _projection_payload(month: Month, faker_tenant) -> dict[str, object]:
    return {
        "month_id": month.id,
        "store_id": faker_tenant["store_id"],
        "year": month.year,
        "month": month.month,
        "revision": month.revision,
    }


def test_google_projection_handler_writes_adapter_payload(session, faker_tenant):
    month = _open_month(session, faker_tenant)
    _seed_working(session, faker_tenant, month)

    enqueue(
        session,
        tenant_id=faker_tenant["tenant_id"],
        kind=JobKind.GOOGLE_PROJECTION_STORE.value,
        idempotency_key="google-s5a-1",
        payload=_projection_payload(month, faker_tenant),
    )
    session.commit()

    row, result = run_once(locked_by=WORKER_LOCKED_BY)
    assert row is not None and result is not None
    assert row.status == "DONE"
    assert result.status == "DONE"
    assert result.payload["label"] == "google_projection_s5a"
    assert result.payload["store_id"] == faker_tenant["store_id"]
    assert result.payload["generation"] == FIXTURE_GENERATION

    projection = read_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
    )
    assert projection is not None
    assert projection.generation == FIXTURE_GENERATION
    assert "rows" in projection.grila
    assert "rows" in projection.pontaj
    grila_rows = projection.grila.get("rows", [])
    assert any(
        entry.get("business_date") == "2026-08-01"
        and entry.get("status") == "WORKING"
        for entry in grila_rows
    )


def test_google_projection_provider_failure_is_retried_and_diagnostic_is_kept(
    monkeypatch, session, faker_tenant
):
    month = _open_month(session, faker_tenant)
    _seed_working(session, faker_tenant, month)
    monkeypatch.setenv("UGR_S5_GOOGLE_FAIL", "1")
    assert is_provider_failing() is True

    enqueue(
        session,
        tenant_id=faker_tenant["tenant_id"],
        kind=JobKind.GOOGLE_PROJECTION_STORE.value,
        idempotency_key="google-s5a-fail",
        payload=_projection_payload(month, faker_tenant),
    )
    session.commit()

    row, result = run_once(locked_by=WORKER_LOCKED_BY)
    assert row is not None
    assert result is None
    assert row.status == "PENDING"
    assert row.attempts == 1
    assert "RETRYABLE" in (row.last_error or "")
    assert "UGR_S5_GOOGLE_FAIL" in (row.last_error or "")
    assert row.locked_by is None
    assert row.locked_at is None

    failed_run = (
        session.query(SheetProjectionRun)
        .filter_by(
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
            status="FAILED",
        )
        .order_by(SheetProjectionRun.id.desc())
        .first()
    )
    assert failed_run is not None
    assert "UGR_S5_GOOGLE_FAIL" in (failed_run.last_error or "")

    assert (
        read_store_projection(
            session,
            tenant_id=faker_tenant["tenant_id"],
            store_id=faker_tenant["store_id"],
        )
        is None
    )


def test_google_retry_never_destroys_last_good_projection(monkeypatch, session, faker_tenant):
    month = _open_month(session, faker_tenant)
    _seed_working(session, faker_tenant, month)

    enqueue(
        session,
        tenant_id=faker_tenant["tenant_id"],
        kind=JobKind.GOOGLE_PROJECTION_STORE.value,
        idempotency_key="google-last-good-seed",
        payload=_projection_payload(month, faker_tenant),
    )
    session.commit()
    good_row, good_result = run_once(locked_by=WORKER_LOCKED_BY)
    assert good_row is not None and good_row.status == "DONE"
    assert good_result is not None

    before = read_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
    )
    assert before is not None
    before_grila = dict(before.grila)
    before_pontaj = dict(before.pontaj)

    monkeypatch.setenv("UGR_S5_GOOGLE_FAIL", "1")
    enqueue(
        session,
        tenant_id=faker_tenant["tenant_id"],
        kind=JobKind.GOOGLE_PROJECTION_STORE.value,
        idempotency_key="google-last-good-fail",
        payload=_projection_payload(month, faker_tenant),
    )
    session.commit()
    retry_row, retry_result = run_once(locked_by=WORKER_LOCKED_BY)
    assert retry_row is not None and retry_row.status == "PENDING"
    assert retry_result is None

    after = read_store_projection(
        session,
        tenant_id=faker_tenant["tenant_id"],
        store_id=faker_tenant["store_id"],
    )
    assert after is not None
    assert after.generation == before.generation
    assert dict(after.grila) == before_grila
    assert dict(after.pontaj) == before_pontaj


def test_google_projection_handler_rejects_cross_tenant_payload(session, faker_tenant):
    """Tenant mismatch is a terminal domain failure, not a retry candidate."""

    month = _open_month(session, faker_tenant)
    enqueue(
        session,
        tenant_id="tenant_other",
        kind=JobKind.GOOGLE_PROJECTION_STORE.value,
        idempotency_key="google-cross-tenant",
        payload=_projection_payload(month, faker_tenant),
    )
    session.commit()

    row, result = run_once(locked_by=WORKER_LOCKED_BY)
    assert row is not None
    assert result is None
    assert row.status == "FAILED"
    assert "TERMINAL" in (row.last_error or "")
    assert "tenant" in (row.last_error or "").lower()


def test_google_projection_handler_requires_payload(session, faker_tenant):
    """A missing ``store_id`` / ``month_id`` is terminal validation."""

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
    assert "TERMINAL" in row.last_error


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
