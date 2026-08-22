from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ugrile.core import database
from ugrile.core.correlation import (
    CORRELATION_HEADER,
    accepted_correlation_id,
    current_correlation_id,
)
from ugrile.domain.enums import MonthState
from ugrile.repositories.models import AuditEvent, OutboxJob
from ugrile.repositories.months import MonthRepository
from ugrile.services.audit import record_audit_event
from ugrile.worker import worker as worker_module
from ugrile.worker.jobs import JobResult

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}


def _open_month(faker_tenant) -> str:
    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
        month.state = MonthState.OPEN.value
        session.commit()
        return month.id


def test_request_correlation_is_echoed_on_success_and_error(client):
    correlation_id = "client.req-123"

    success = client.get("/version", headers={CORRELATION_HEADER: correlation_id})
    assert success.status_code == 200
    assert success.headers[CORRELATION_HEADER] == correlation_id

    error = client.get("/session", headers={CORRELATION_HEADER: correlation_id})
    assert error.status_code == 401
    assert error.headers[CORRELATION_HEADER] == correlation_id
    assert current_correlation_id() is None


def test_invalid_request_correlation_is_replaced(client):
    response = client.get("/version", headers={CORRELATION_HEADER: "bad value with spaces"})

    assert response.status_code == 200
    generated = response.headers[CORRELATION_HEADER]
    assert generated != "bad value with spaces"
    assert generated.startswith("req_")
    assert accepted_correlation_id(generated) == generated


def test_request_correlation_overrides_calendar_audit_only_id(client, faker_tenant):
    month_id = _open_month(faker_tenant)
    correlation_id = "corr-calendar-001"

    response = client.post(
        f"/months/{month_id}/calendar/apply",
        headers={**ADMIN, CORRELATION_HEADER: correlation_id},
        json={
            "expected_revision": 0,
            "changes": [
                {
                    "person_id": faker_tenant["person_a_id"],
                    "business_date": "2026-08-01",
                    "status": "WORKING",
                    "store_id": faker_tenant["store_id"],
                    "working_kind": "NORMAL",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers[CORRELATION_HEADER] == correlation_id

    with database.session_scope() as session:
        audit = session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.tenant_id == faker_tenant["tenant_id"],
                AuditEvent.action == "CALENDAR_APPLY",
                AuditEvent.entity_id == month_id,
            )
            .order_by(AuditEvent.id.desc())
        ).scalars().first()
        assert audit is not None
        payload = json.loads(audit.payload)
        assert payload["correlation_id"] == correlation_id


def test_request_correlation_survives_durable_job_and_worker_dispatch(
    client,
    faker_tenant,
    monkeypatch,
):
    month_id = _open_month(faker_tenant)
    correlation_id = "corr-job-001"

    response = client.post(
        f"/months/{month_id}/sheet-projection/enqueue",
        headers={**ADMIN, CORRELATION_HEADER: correlation_id},
        json={
            "store_id": faker_tenant["store_id"],
            "idempotency_key": "corr-job-proof",
        },
    )
    assert response.status_code == 200, response.text
    assert response.headers[CORRELATION_HEADER] == correlation_id

    with database.session_scope() as session:
        job = session.execute(
            select(OutboxJob).where(
                OutboxJob.tenant_id == faker_tenant["tenant_id"],
                OutboxJob.idempotency_key == "corr-job-proof",
            )
        ).scalar_one()
        persisted_payload = json.loads(job.payload)
        assert persisted_payload["correlation_id"] == correlation_id

    assert current_correlation_id() is None

    def _handler(
        session: Session,
        tenant_id: str,
        payload: dict[str, Any],
    ) -> JobResult:
        assert tenant_id == faker_tenant["tenant_id"]
        assert payload["correlation_id"] == correlation_id
        assert current_correlation_id() == correlation_id
        record_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=None,
            action="WORKER_CORRELATION_TEST",
            entity="outbox_job",
            entity_id="corr-job-proof",
            payload={"worker": True},
        )
        return JobResult(status="DONE", payload={"ok": True})

    monkeypatch.setattr(worker_module, "get_handler", lambda _kind: _handler)
    row, result = worker_module.run_once(locked_by="correlation-test-worker")

    assert row is not None
    assert row.status == "DONE"
    assert result is not None
    assert current_correlation_id() is None

    with database.session_scope() as session:
        audit = session.execute(
            select(AuditEvent).where(
                AuditEvent.tenant_id == faker_tenant["tenant_id"],
                AuditEvent.action == "WORKER_CORRELATION_TEST",
            )
        ).scalar_one()
        payload = json.loads(audit.payload)
        assert payload == {"correlation_id": correlation_id, "worker": True}
