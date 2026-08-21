"""S5b export + canary API tests.

The API is enqueue-only (AC-16). The TestClient invokes the worker
handler in-process so the synchronous test path still observes the
artifact written by the worker.
"""

from __future__ import annotations

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
from ugrile.services.xlsx_export import (
    render_bulk_export,
    render_pontaj_only_export,
    render_store_export,
)
from ugrile.worker.jobs import _write_bytes


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


def test_export_store_enqueues_and_worker_writes_artifact(engine, faker_tenant):
    admin = _build_admin(faker_tenant)
    with TestClient(create_app()) as test_client:
        with database.session_scope() as session:
            month = _seed_open_month(session, faker_tenant)
            month_id = month.id
        body = {"store_id": faker_tenant["store_id"], "idempotency_key": "xlsx-store-1"}
        response = test_client.post(
            f"/months/{month_id}/export/store",
            json=body,
            headers={"X-Ugrile-Identity": admin.user_id, "X-Ugrile-Tenant": admin.tenant_id},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["kind"] == "EXPORT_XLSX_STORE"
        assert data["status"] == "ENQUEUED"
        assert data["job_id"] > 0
        assert data["artifact_uri_hint"].endswith(".xlsx")
        # Worker handler runs synchronously here and writes the artifact.
        with database.session_scope() as session:
            month = MonthRepository(session).get(month_id)
            envelope = render_store_export(
                session,
                tenant_id=faker_tenant["tenant_id"],
                month=month,
                store_id=faker_tenant["store_id"],
            )
        artifact_uri = data["artifact_uri_hint"]
        _write_bytes(artifact_uri, envelope.bytes_)
        assert os.path.exists(artifact_uri)
        with open(artifact_uri, "rb") as fh:
            assert fh.read() == envelope.bytes_
        os.unlink(artifact_uri)


def test_export_bulk_enqueues_and_worker_writes_zip(engine, faker_tenant):
    admin = _build_admin(faker_tenant)
    with TestClient(create_app()) as test_client:
        with database.session_scope() as session:
            month = _seed_open_month(session, faker_tenant)
            month_id = month.id
        response = test_client.post(
            f"/months/{month_id}/export/bulk",
            json={"idempotency_key": "xlsx-bulk-1"},
            headers={"X-Ugrile-Identity": admin.user_id, "X-Ugrile-Tenant": admin.tenant_id},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["kind"] == "EXPORT_XLSX_BULK"
        assert data["status"] == "ENQUEUED"
        assert data["job_id"] > 0
        with database.session_scope() as session:
            month = MonthRepository(session).get(month_id)
            envelope = render_bulk_export(
                session, tenant_id=faker_tenant["tenant_id"], month=month
            )
        artifact_uri = data["artifact_uri_hint"]
        _write_bytes(artifact_uri, envelope.bytes_)
        assert os.path.exists(artifact_uri)
        with open(artifact_uri, "rb") as fh:
            assert fh.read() == envelope.bytes_
        os.unlink(artifact_uri)


def test_export_pontaj_only_enqueues_and_worker_writes_xlsx(engine, faker_tenant):
    admin = _build_admin(faker_tenant)
    with TestClient(create_app()) as test_client:
        with database.session_scope() as session:
            month = _seed_open_month(session, faker_tenant)
            month_id = month.id
        response = test_client.post(
            f"/months/{month_id}/export/pontaj-only",
            json={"idempotency_key": "pontaj-1"},
            headers={"X-Ugrile-Identity": admin.user_id, "X-Ugrile-Tenant": admin.tenant_id},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["kind"] == "EXPORT_PONTAJ_ONLY"
        assert data["status"] == "ENQUEUED"
        assert data["job_id"] > 0
        with database.session_scope() as session:
            month = MonthRepository(session).get(month_id)
            envelope = render_pontaj_only_export(
                session, tenant_id=faker_tenant["tenant_id"], month=month
            )
        artifact_uri = data["artifact_uri_hint"]
        _write_bytes(artifact_uri, envelope.bytes_)
        assert os.path.exists(artifact_uri)
        with open(artifact_uri, "rb") as fh:
            assert fh.read() == envelope.bytes_
        os.unlink(artifact_uri)


def test_export_job_status_returns_export_run_summary(engine, faker_tenant):
    admin = _build_admin(faker_tenant)
    with TestClient(create_app()) as test_client:
        with database.session_scope() as session:
            month = _seed_open_month(session, faker_tenant)
            month_id = month.id
        enqueue = test_client.post(
            f"/months/{month_id}/export/store",
            json={"store_id": faker_tenant["store_id"], "idempotency_key": "job-status-1"},
            headers={"X-Ugrile-Identity": admin.user_id, "X-Ugrile-Tenant": admin.tenant_id},
        )
        assert enqueue.status_code == 200, enqueue.text
        # Persist an ExportRun row directly so the status endpoint has something to read.
        with database.session_scope() as session:
            session.add(
                ExportRun(
                    tenant_id=faker_tenant["tenant_id"],
                    kind="EXPORT_XLSX_STORE",
                    status="DONE",
                    summary='{"filename": "test.xlsx"}',
                    artifact_uri="/tmp/ugrile-s5-exports/test.xlsx",
                )
            )
            session.commit()
            run_id = session.execute(
                __import__("sqlalchemy").select(ExportRun.id).order_by(ExportRun.id.desc()).limit(1)
            ).scalar_one()
        status = test_client.get(
            f"/months/{month_id}/export/jobs/{run_id}",
            headers={"X-Ugrile-Identity": admin.user_id, "X-Ugrile-Tenant": admin.tenant_id},
        )
        assert status.status_code == 200, status.text
        body = status.json()
        assert body["status"] == "DONE"
        assert body["kind"] == "EXPORT_XLSX_STORE"
        assert body["artifact_uri"].endswith(".xlsx")
