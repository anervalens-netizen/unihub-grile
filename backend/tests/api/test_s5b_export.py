"""S5b export + canary API tests.

AC-16 contract: the export enqueue endpoint returns the ``ExportRun.id``
of a pre-created PENDING row. The durable worker updates the same row
to ``DONE`` (or ``FAILED``), and the polling endpoint reads it back by
that id. The test client drives the worker in-process via
``worker.run_once`` so the end-to-end contract is exercised
synchronously.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date

from fastapi.testclient import TestClient

from ugrile.core import database
from ugrile.domain.enums import DayStatus, MonthState, RoleName, WorkingKind
from ugrile.main import create_app
from ugrile.repositories.models import ExportRun
from ugrile.repositories.months import MonthRepository
from ugrile.services.auth import Principal
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.worker.worker import WORKER_LOCKED_BY, run_once


def _build_admin(faker_tenant) -> Principal:
    return Principal(
        user_id="user_admin",
        tenant_id=faker_tenant["tenant_id"],
        role=RoleName.ADMIN,
        email="admin@acme.example",
    )


def _seed_open_month(session, faker_tenant):
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    CalendarService(session).apply(
        month=month,
        tenant_id=faker_tenant["tenant_id"],
        expected_revision=month.revision,
        changes=[
            CalendarChange(
                faker_tenant["person_a_id"],
                date(2026, 8, 1),
                faker_tenant["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    session.commit()
    return month


def _drain_worker_until_empty() -> int:
    """Run the worker until it returns ``None`` (queue empty).

    Returns the number of jobs processed.
    """

    processed = 0
    while True:
        row, _result = run_once(locked_by=WORKER_LOCKED_BY)
        if row is None:
            return processed
        processed += 1


def _enqueue_export(
    test_client,
    admin: Principal,
    month_id: str,
    *,
    path: str,
    body: dict,
) -> dict:
    response = test_client.post(
        f"/months/{month_id}{path}",
        json=body,
        headers={"X-Ugrile-Identity": admin.user_id, "X-Ugrile-Tenant": admin.tenant_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _poll_export(test_client, admin: Principal, month_id: str, job_id: int) -> dict:
    status = test_client.get(
        f"/months/{month_id}/export/jobs/{job_id}",
        headers={"X-Ugrile-Identity": admin.user_id, "X-Ugrile-Tenant": admin.tenant_id},
    )
    assert status.status_code == 200, status.text
    return status.json()


def _cleanup_artifact(artifact_uri: str | None) -> None:
    if artifact_uri and os.path.exists(artifact_uri):
        os.unlink(artifact_uri)


def _internal_artifact_uri(job_id: int) -> str | None:
    with database.session_scope() as session:
        run = session.get(ExportRun, job_id)
        assert run is not None
        return run.artifact_uri


def test_export_store_enqueues_with_export_run_id_and_artifact_hint(engine, faker_tenant):
    admin = _build_admin(faker_tenant)
    with TestClient(create_app()) as test_client:
        with database.session_scope() as session:
            month = _seed_open_month(session, faker_tenant)
            month_id = month.id
        data = _enqueue_export(
            test_client,
            admin,
            month_id,
            path="/export/store",
            body={"store_id": faker_tenant["store_id"], "idempotency_key": "xlsx-store-1"},
        )
        assert data["kind"] == "EXPORT_XLSX_STORE"
        assert data["status"] == "ENQUEUED"
        assert data["job_id"] > 0
        assert "artifact_uri_hint" not in data
        assert data["artifact_ready"] is False
        assert data["month_revision"] == data["data_revision"] == 1
        # The same job_id must be readable as an ExportRun row in PENDING.
        pending = _poll_export(test_client, admin, month_id, data["job_id"])
        assert pending["status"] == "PENDING"
        assert pending["kind"] == "EXPORT_XLSX_STORE"
        assert "artifact_uri" not in pending
        assert pending["artifact_ready"] is False


def test_export_store_polling_returns_done_with_artifact_and_checksum(engine, faker_tenant):
    """AC-16 happy path: enqueue → poll same id → DONE → artifact + checksum."""
    admin = _build_admin(faker_tenant)
    with TestClient(create_app()) as test_client:
        with database.session_scope() as session:
            month = _seed_open_month(session, faker_tenant)
            month_id = month.id
        data = _enqueue_export(
            test_client,
            admin,
            month_id,
            path="/export/store",
            body={"store_id": faker_tenant["store_id"], "idempotency_key": "xlsx-store-poll"},
        )
        job_id = data["job_id"]
        processed = _drain_worker_until_empty()
        assert processed == 1
        body = _poll_export(test_client, admin, month_id, job_id)
        assert body["status"] == "DONE"
        assert body["kind"] == "EXPORT_XLSX_STORE"
        assert "artifact_uri" not in body
        assert body["artifact_ready"] is True
        artifact_uri = _internal_artifact_uri(job_id)
        assert artifact_uri is not None
        assert os.path.exists(artifact_uri)
        try:
            with open(artifact_uri, "rb") as fh:
                workbook_bytes = fh.read()
            assert workbook_bytes[:2] == b"PK"  # zip header for xlsx
            assert "filename" in body["summary"]
            summary = json.loads(body["summary"])
            assert summary["filename"].endswith(".xlsx")
            assert "checksum_sha256" in summary
            assert summary["summary"]["revision"] == data["data_revision"]
            assert hashlib.sha256(workbook_bytes).hexdigest() == summary["checksum_sha256"]
        finally:
            _cleanup_artifact(artifact_uri)


def test_export_download_streams_done_artifact_and_rejects_pending_or_unknown(engine, faker_tenant):
    """The stable ExportRun id downloads exactly the worker artifact."""
    admin = _build_admin(faker_tenant)
    headers = {
        "X-Ugrile-Identity": admin.user_id,
        "X-Ugrile-Tenant": admin.tenant_id,
    }
    with TestClient(create_app()) as test_client:
        with database.session_scope() as session:
            month = _seed_open_month(session, faker_tenant)
            month_id = month.id
        data = _enqueue_export(
            test_client,
            admin,
            month_id,
            path="/export/store",
            body={"store_id": faker_tenant["store_id"], "idempotency_key": "xlsx-download"},
        )
        pending = test_client.get(
            f"/months/{month_id}/export/jobs/{data['job_id']}/download", headers=headers
        )
        assert pending.status_code == 404
        assert pending.json()["details"]["code"] == "EXPORT_ARTIFACT_NOT_FOUND"

        assert _drain_worker_until_empty() == 1
        status = _poll_export(test_client, admin, month_id, data["job_id"])
        assert "artifact_uri" not in status
        assert status["artifact_ready"] is True
        artifact_uri = _internal_artifact_uri(data["job_id"])
        assert artifact_uri is not None
        try:
            with open(artifact_uri, "rb") as fh:
                expected = fh.read()
            downloaded = test_client.get(
                f"/months/{month_id}/export/jobs/{data['job_id']}/download", headers=headers
            )
            assert downloaded.status_code == 200, downloaded.text
            assert downloaded.content == expected
            assert downloaded.headers["content-type"].startswith(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            assert downloaded.headers["content-disposition"].startswith("attachment;")
            assert hashlib.sha256(downloaded.content).hexdigest() == json.loads(
                status["summary"]
            )["checksum_sha256"]
        finally:
            _cleanup_artifact(artifact_uri)

        unknown = test_client.get(
            f"/months/{month_id}/export/jobs/999999/download", headers=headers
        )
        assert unknown.status_code == 404
        assert unknown.json()["details"]["code"] == "EXPORT_JOB_NOT_FOUND"


def test_export_bulk_polling_returns_done_with_zip_artifact(engine, faker_tenant):
    admin = _build_admin(faker_tenant)
    with TestClient(create_app()) as test_client:
        with database.session_scope() as session:
            month = _seed_open_month(session, faker_tenant)
            month_id = month.id
        data = _enqueue_export(
            test_client,
            admin,
            month_id,
            path="/export/bulk",
            body={"idempotency_key": "xlsx-bulk-poll"},
        )
        job_id = data["job_id"]
        processed = _drain_worker_until_empty()
        assert processed == 1
        body = _poll_export(test_client, admin, month_id, job_id)
        assert body["status"] == "DONE"
        assert body["kind"] == "EXPORT_XLSX_BULK"
        assert "artifact_uri" not in body
        assert body["artifact_ready"] is True
        artifact_uri = _internal_artifact_uri(job_id)
        assert artifact_uri is not None
        assert artifact_uri.endswith(".zip")
        try:
            assert os.path.exists(artifact_uri)
            with open(artifact_uri, "rb") as fh:
                zip_bytes = fh.read()
            assert zip_bytes[:2] == b"PK"
            summary = json.loads(body["summary"])
            assert "checksum_sha256" in summary
            assert summary["summary"]["revision"] == data["data_revision"]
            assert hashlib.sha256(zip_bytes).hexdigest() == summary["checksum_sha256"]
        finally:
            _cleanup_artifact(artifact_uri)


def test_export_pontaj_only_polling_returns_done_with_xlsx(engine, faker_tenant):
    admin = _build_admin(faker_tenant)
    with TestClient(create_app()) as test_client:
        with database.session_scope() as session:
            month = _seed_open_month(session, faker_tenant)
            month_id = month.id
        data = _enqueue_export(
            test_client,
            admin,
            month_id,
            path="/export/pontaj-only",
            body={"idempotency_key": "xlsx-pontaj-poll"},
        )
        job_id = data["job_id"]
        processed = _drain_worker_until_empty()
        assert processed == 1
        body = _poll_export(test_client, admin, month_id, job_id)
        assert body["status"] == "DONE"
        assert body["kind"] == "EXPORT_PONTAJ_ONLY"
        assert "artifact_uri" not in body
        assert body["artifact_ready"] is True
        artifact_uri = _internal_artifact_uri(job_id)
        assert artifact_uri is not None
        try:
            assert os.path.exists(artifact_uri)
            with open(artifact_uri, "rb") as fh:
                workbook_bytes = fh.read()
            assert workbook_bytes[:2] == b"PK"
            summary = json.loads(body["summary"])
            assert summary["summary"]["revision"] == data["data_revision"]
            assert hashlib.sha256(workbook_bytes).hexdigest() == summary["checksum_sha256"]
        finally:
            _cleanup_artifact(artifact_uri)


def test_export_job_polling_marks_failed_when_handler_raises(engine, faker_tenant):
    """An unknown month_id moves both OutboxJob and ExportRun to FAILED.

    The API enqueue path validates the month before enqueueing, so we
    drive the failure path through the worker's direct enqueue helper
    (the same primitive the API uses internally) to exercise the
    ExportRun FAILED transition that the worker now performs before
    re-raising.
    """

    from ugrile.domain.enums import JobKind
    from ugrile.services.xlsx_export import create_pending_export_run
    from ugrile.worker.worker import enqueue as worker_enqueue

    admin = _build_admin(faker_tenant)
    with TestClient(create_app()) as test_client:
        with database.session_scope() as session:
            month = _seed_open_month(session, faker_tenant)
            month_id = month.id
            run = create_pending_export_run(
                session,
                tenant_id=faker_tenant["tenant_id"],
                kind=JobKind.EXPORT_XLSX_STORE.value,
            )
            session.flush()
            bogus_run_id = run.id
            worker_enqueue(
                session,
                tenant_id=faker_tenant["tenant_id"],
                kind=JobKind.EXPORT_XLSX_STORE.value,
                idempotency_key=f"xlsx-bad-month-{bogus_run_id}",
                payload={
                    "store_id": faker_tenant["store_id"],
                    "month_id": "month_does_not_exist",
                    "month_revision": 0,
                    "data_revision": 0,
                    "export_run_id": bogus_run_id,
                },
            )
            session.commit()
        processed = _drain_worker_until_empty()
        assert processed == 1
        body = _poll_export(test_client, admin, month_id, bogus_run_id)
        assert body["status"] == "FAILED"
        assert "artifact_uri" not in body
        assert body["artifact_ready"] is False
        assert _internal_artifact_uri(bogus_run_id) is None
        summary = json.loads(body["summary"])
        assert "errors" in summary
        assert "month does not exist" in summary["errors"]


def test_export_job_polling_404_for_unknown_id(engine, faker_tenant):
    admin = _build_admin(faker_tenant)
    with TestClient(create_app()) as test_client:
        with database.session_scope() as session:
            month = _seed_open_month(session, faker_tenant)
            month_id = month.id
        response = test_client.get(
            f"/months/{month_id}/export/jobs/999999",
            headers={"X-Ugrile-Identity": admin.user_id, "X-Ugrile-Tenant": admin.tenant_id},
        )
        assert response.status_code == 404, response.text
        body = response.json()
        assert body["details"]["code"] == "EXPORT_JOB_NOT_FOUND"


def test_export_default_idempotency_key_is_revision_bound(engine, faker_tenant):
    """Default idempotency identity includes both month and data revision."""

    admin = _build_admin(faker_tenant)
    with TestClient(create_app()) as test_client:
        with database.session_scope() as session:
            month = _seed_open_month(session, faker_tenant)
            month_id = month.id
        first = _enqueue_export(
            test_client,
            admin,
            month_id,
            path="/export/store",
            body={"store_id": faker_tenant["store_id"]},
        )
        assert first["month_revision"] == first["data_revision"] == 1
        assert first["idempotency_key"] == (
            f"store::{faker_tenant['store_id']}::{month_id}::m1::d1"
        )
        assert first["status"] == "ENQUEUED"
        assert first["job_id"] > 0
        body = _poll_export(test_client, admin, month_id, first["job_id"])
        assert body["job_id"] == first["job_id"]
        assert body["status"] in {"PENDING", "DONE", "FAILED"}
