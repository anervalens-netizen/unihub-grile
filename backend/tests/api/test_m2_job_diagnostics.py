"""JOB-009 diagnostics and tenant/resource-scope regression tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from ugrile.core import database
from ugrile.domain.enums import JobKind, MonthState, RoleName
from ugrile.main import create_app
from ugrile.repositories.models import ManagerScope, User
from ugrile.repositories.months import MonthRepository
from ugrile.worker.worker import enqueue

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}


def _month(session, tenant_id: str):
    month = MonthRepository(session).get_or_create(tenant_id, 2026, 8)
    month.state = MonthState.OPEN
    session.flush()
    return month


def _enqueue_scoped(
    session,
    *,
    tenant_id: str,
    month_id: str,
    store_id: str,
    key: str,
):
    return enqueue(
        session,
        tenant_id=tenant_id,
        kind=JobKind.GOOGLE_PROJECTION_STORE.value,
        idempotency_key=key,
        payload={"month_id": month_id, "store_id": store_id},
    )


def _add_manager(session, faker_tenant, *, user_id: str) -> dict[str, str]:
    session.add(
        User(
            id=user_id,
            tenant_id=faker_tenant["tenant_id"],
            email=f"{user_id}@acme.example",
            display_name="Manager",
            role=RoleName.MANAGER.value,
        )
    )
    session.flush()
    session.add(
        ManagerScope(
            tenant_id=faker_tenant["tenant_id"],
            user_id=user_id,
            store_id=faker_tenant["store_id"],
            effective_from=date(2026, 8, 1),
            effective_to=date(2026, 8, 31),
        )
    )
    return {
        "X-Ugrile-Identity": user_id,
        "X-Ugrile-Tenant": faker_tenant["tenant_id"],
    }


def test_legacy_job_list_is_tenant_scoped(engine, faker_tenant, faker_second_tenant):
    with database.session_scope() as session:
        enqueue(
            session,
            tenant_id=faker_tenant["tenant_id"],
            kind=JobKind.NOOP.value,
            idempotency_key="acme-only",
        )
        enqueue(
            session,
            tenant_id=faker_second_tenant["tenant_id"],
            kind=JobKind.NOOP.value,
            idempotency_key="beta-only",
        )
        session.commit()

    with TestClient(create_app()) as client:
        response = client.get("/worker/jobs", headers=ADMIN)

    assert response.status_code == 200, response.text
    jobs = response.json()
    assert [row["idempotency_key"] for row in jobs] == ["acme-only"]
    assert all(row["tenant_id"] == faker_tenant["tenant_id"] for row in jobs)


def test_diagnostics_classify_active_retry_and_terminal_state(engine, faker_tenant):
    now = datetime.now(tz=UTC)
    with database.session_scope() as session:
        month = _month(session, faker_tenant["tenant_id"])
        jobs = [
            _enqueue_scoped(
                session,
                tenant_id=faker_tenant["tenant_id"],
                month_id=month.id,
                store_id=faker_tenant["store_id"],
                key=f"diag-{index}",
            )
            for index in range(5)
        ]
        jobs[1].attempts = 1
        jobs[1].run_after = now + timedelta(minutes=5)
        jobs[2].status = "RUNNING"
        jobs[2].attempts = 1
        jobs[2].locked_at = now
        jobs[2].locked_by = "worker-a"
        jobs[3].status = "FAILED"
        jobs[3].attempts = 3
        jobs[3].last_error = "RETRY_EXHAUSTED: provider timeout"
        jobs[4].status = "DONE"
        jobs[4].attempts = 1
        session.commit()

    with TestClient(create_app()) as client:
        response = client.get("/worker/jobs/diagnostics", headers=ADMIN)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["counts"] == {
        "queued": 1,
        "retrying": 1,
        "running": 1,
        "failed": 1,
        "done": 1,
    }
    assert {row["state"] for row in body["jobs"]} == {
        "QUEUED",
        "RETRY",
        "RUNNING",
        "FAILED",
        "DONE",
    }
    for row in body["jobs"]:
        assert row["month_id"] == month.id
        assert row["store_ids"] == [faker_tenant["store_id"]]
        assert "payload" not in row
        assert "idempotency_key" not in row
        assert row["max_attempts"] == 5


def test_manager_diagnostics_fail_closed_outside_effective_store_scope(engine, faker_tenant):
    with database.session_scope() as session:
        month = _month(session, faker_tenant["tenant_id"])
        manager_headers = _add_manager(session, faker_tenant, user_id="user_manager")
        visible = _enqueue_scoped(
            session,
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["store_id"],
            key="manager-visible",
        )
        hidden = _enqueue_scoped(
            session,
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["other_store_id"],
            key="manager-hidden",
        )
        unscoped = enqueue(
            session,
            tenant_id=faker_tenant["tenant_id"],
            kind=JobKind.NOOP.value,
            idempotency_key="manager-unscoped",
        )
        session.commit()
        visible_id = visible.id
        assert hidden.id != visible_id
        assert unscoped.id != visible_id

    with TestClient(create_app()) as client:
        response = client.get("/worker/jobs/diagnostics", headers=manager_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["counts"]["queued"] == 1
    assert [row["id"] for row in body["jobs"]] == [visible_id]


def test_manager_terminal_limit_is_applied_after_scope_filter(engine, faker_tenant):
    """Newer foreign-scope history cannot crowd out a visible terminal job."""

    with database.session_scope() as session:
        month = _month(session, faker_tenant["tenant_id"])
        manager_headers = _add_manager(session, faker_tenant, user_id="user_history_manager")
        visible = _enqueue_scoped(
            session,
            tenant_id=faker_tenant["tenant_id"],
            month_id=month.id,
            store_id=faker_tenant["store_id"],
            key="history-visible",
        )
        visible.status = "DONE"
        visible.attempts = 1
        session.flush()
        visible_id = visible.id

        for index in range(3):
            hidden = _enqueue_scoped(
                session,
                tenant_id=faker_tenant["tenant_id"],
                month_id=month.id,
                store_id=faker_tenant["other_store_id"],
                key=f"history-hidden-{index}",
            )
            hidden.status = "DONE"
            hidden.attempts = 1
        session.commit()

    with TestClient(create_app()) as client:
        response = client.get(
            "/worker/jobs/diagnostics?terminal_limit=1",
            headers=manager_headers,
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["counts"]["done"] == 1
    assert [row["id"] for row in body["jobs"]] == [visible_id]


def test_readonly_cannot_read_job_diagnostics(engine, faker_tenant):
    headers = {
        "X-Ugrile-Identity": "user_readonly",
        "X-Ugrile-Tenant": faker_tenant["tenant_id"],
    }
    with database.session_scope() as session:
        session.add(
            User(
                id="user_readonly",
                tenant_id=faker_tenant["tenant_id"],
                email="readonly@acme.example",
                display_name="Readonly",
                role=RoleName.READONLY.value,
            )
        )
        session.commit()

    with TestClient(create_app()) as client:
        response = client.get("/worker/jobs/diagnostics", headers=headers)

    assert response.status_code == 403, response.text
