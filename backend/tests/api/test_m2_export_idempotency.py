"""M2 export idempotency and side-effect isolation tests (JOB-006/JOB-011)."""

from __future__ import annotations

import os
from datetime import date

from fastapi.testclient import TestClient

from ugrile.core import database
from ugrile.domain.enums import DayStatus, JobKind, MonthState, RoleName, WorkingKind
from ugrile.main import create_app
from ugrile.repositories.models import ExportRun, OutboxJob
from ugrile.repositories.months import MonthRepository
from ugrile.services.auth import Principal
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.worker.worker import WORKER_LOCKED_BY, run_once


def _principal(*, user_id: str, tenant_id: str, email: str) -> Principal:
    return Principal(user_id=user_id, tenant_id=tenant_id, role=RoleName.ADMIN, email=email)


def _headers(principal: Principal) -> dict[str, str]:
    return {
        "X-Ugrile-Identity": principal.user_id,
        "X-Ugrile-Tenant": principal.tenant_id,
    }


def _seed_month(session, tenant: dict[str, str]):
    month = MonthRepository(session).get_or_create(tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    CalendarService(session).apply(
        month=month,
        tenant_id=tenant["tenant_id"],
        expected_revision=month.revision,
        changes=[
            CalendarChange(
                tenant.get("person_a_id") or tenant["person_id"],
                date(2026, 8, 1),
                tenant["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    session.commit()
    return month


def _post_store(
    client: TestClient,
    principal: Principal,
    month_id: str,
    *,
    store_id: str,
    key: str,
):
    return client.post(
        f"/months/{month_id}/export/store",
        json={"store_id": store_id, "idempotency_key": key},
        headers=_headers(principal),
    )


def _cleanup(*paths: str | None) -> None:
    for path in paths:
        if path and os.path.exists(path):
            os.unlink(path)


def test_exact_replay_returns_same_export_run_before_and_after_completion(engine, faker_tenant):
    principal = _principal(
        user_id="user_admin",
        tenant_id=faker_tenant["tenant_id"],
        email="admin@acme.example",
    )
    with TestClient(create_app()) as client:
        with database.session_scope() as session:
            month = _seed_month(session, faker_tenant)
            month_id = month.id

        first = _post_store(
            client,
            principal,
            month_id,
            store_id=faker_tenant["store_id"],
            key="m2-exact-replay",
        )
        second = _post_store(
            client,
            principal,
            month_id,
            store_id=faker_tenant["store_id"],
            key="m2-exact-replay",
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        first_body = first.json()
        second_body = second.json()
        assert first_body["replayed"] is False
        assert second_body["replayed"] is True
        assert second_body["job_id"] == first_body["job_id"]
        assert second_body["artifact_uri_hint"] == first_body["artifact_uri_hint"]

        with database.session_scope() as session:
            assert (
                session.query(OutboxJob)
                .filter_by(
                    tenant_id=faker_tenant["tenant_id"],
                    kind=JobKind.EXPORT_XLSX_STORE.value,
                    idempotency_key="m2-exact-replay",
                )
                .count()
                == 1
            )
            assert (
                session.query(ExportRun)
                .filter_by(
                    tenant_id=faker_tenant["tenant_id"],
                    kind=JobKind.EXPORT_XLSX_STORE.value,
                )
                .count()
                == 1
            )

        row, result = run_once(locked_by=WORKER_LOCKED_BY)
        assert row is not None and row.status == "DONE"
        assert result is not None and result.status == "DONE"

        replay_done = _post_store(
            client,
            principal,
            month_id,
            store_id=faker_tenant["store_id"],
            key="m2-exact-replay",
        )
        assert replay_done.status_code == 200, replay_done.text
        replay_body = replay_done.json()
        assert replay_body["replayed"] is True
        assert replay_body["job_id"] == first_body["job_id"]
        assert replay_body["status"] == "DONE"
        _cleanup(replay_body["artifact_uri_hint"])


def test_same_key_with_different_payload_fails_closed_without_orphan_run(engine, faker_tenant):
    principal = _principal(
        user_id="user_admin",
        tenant_id=faker_tenant["tenant_id"],
        email="admin@acme.example",
    )
    with TestClient(create_app()) as client:
        with database.session_scope() as session:
            month = _seed_month(session, faker_tenant)
            month_id = month.id

        first = _post_store(
            client,
            principal,
            month_id,
            store_id=faker_tenant["store_id"],
            key="m2-semantic-conflict",
        )
        assert first.status_code == 200, first.text
        conflict = _post_store(
            client,
            principal,
            month_id,
            store_id=faker_tenant["other_store_id"],
            key="m2-semantic-conflict",
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["details"]["code"] == "EXPORT_IDEMPOTENCY_KEY_REUSED"

        with database.session_scope() as session:
            assert session.query(ExportRun).count() == 1
            assert session.query(OutboxJob).count() == 1


def test_same_key_is_independent_across_job_kinds(engine, faker_tenant):
    principal = _principal(
        user_id="user_admin",
        tenant_id=faker_tenant["tenant_id"],
        email="admin@acme.example",
    )
    with TestClient(create_app()) as client:
        with database.session_scope() as session:
            month = _seed_month(session, faker_tenant)
            month_id = month.id
        key = "m2-shared-human-key"
        store = _post_store(
            client,
            principal,
            month_id,
            store_id=faker_tenant["store_id"],
            key=key,
        )
        bulk = client.post(
            f"/months/{month_id}/export/bulk",
            json={"store_ids": [faker_tenant["store_id"]], "idempotency_key": key},
            headers=_headers(principal),
        )
        assert store.status_code == 200, store.text
        assert bulk.status_code == 200, bulk.text
        assert store.json()["job_id"] != bulk.json()["job_id"]
        with database.session_scope() as session:
            assert session.query(OutboxJob).filter_by(idempotency_key=key).count() == 2


def test_same_key_is_independent_across_tenants(
    engine,
    faker_tenant,
    faker_second_tenant,
):
    acme = _principal(
        user_id="user_admin",
        tenant_id=faker_tenant["tenant_id"],
        email="admin@acme.example",
    )
    beta = _principal(
        user_id="user_admin_beta",
        tenant_id=faker_second_tenant["tenant_id"],
        email="admin@beta.example",
    )
    with TestClient(create_app()) as client:
        with database.session_scope() as session:
            acme_month = _seed_month(session, faker_tenant)
            beta_month = _seed_month(session, faker_second_tenant)
            acme_month_id = acme_month.id
            beta_month_id = beta_month.id
        key = "m2-cross-tenant-key"
        acme_response = _post_store(
            client,
            acme,
            acme_month_id,
            store_id=faker_tenant["store_id"],
            key=key,
        )
        beta_response = _post_store(
            client,
            beta,
            beta_month_id,
            store_id=faker_second_tenant["store_id"],
            key=key,
        )
        assert acme_response.status_code == 200, acme_response.text
        assert beta_response.status_code == 200, beta_response.text
        assert acme_response.json()["job_id"] != beta_response.json()["job_id"]
        with database.session_scope() as session:
            assert session.query(OutboxJob).filter_by(idempotency_key=key).count() == 2


def test_distinct_export_operations_never_share_or_mutate_artifact(engine, faker_tenant):
    principal = _principal(
        user_id="user_admin",
        tenant_id=faker_tenant["tenant_id"],
        email="admin@acme.example",
    )
    first_path: str | None = None
    second_path: str | None = None
    try:
        with TestClient(create_app()) as client:
            with database.session_scope() as session:
                month = _seed_month(session, faker_tenant)
                month_id = month.id
            first = _post_store(
                client,
                principal,
                month_id,
                store_id=faker_tenant["store_id"],
                key="m2-artifact-a",
            ).json()
            first_path = first["artifact_uri_hint"]
            row, result = run_once(locked_by=WORKER_LOCKED_BY)
            assert row is not None and row.status == "DONE"
            assert result is not None
            with open(first_path, "rb") as fh:
                first_bytes = fh.read()

            second = _post_store(
                client,
                principal,
                month_id,
                store_id=faker_tenant["store_id"],
                key="m2-artifact-b",
            ).json()
            second_path = second["artifact_uri_hint"]
            assert second_path != first_path
            row, result = run_once(locked_by=WORKER_LOCKED_BY)
            assert row is not None and row.status == "DONE"
            assert result is not None

            with open(first_path, "rb") as fh:
                assert fh.read() == first_bytes
            assert os.path.exists(second_path)
    finally:
        _cleanup(first_path, second_path)
