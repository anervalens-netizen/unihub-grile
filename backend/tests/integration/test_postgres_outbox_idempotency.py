"""PostgreSQL proofs for scoped outbox idempotency and concurrent export replay."""

from __future__ import annotations

import threading

import pytest
from sqlalchemy.exc import IntegrityError

from ugrile.api import s5b
from ugrile.domain.enums import JobKind
from ugrile.repositories.models import ExportRun, OutboxJob, Tenant
from ugrile.worker.worker import enqueue

pytestmark = pytest.mark.postgres


def _tenant(tenant_id: str) -> Tenant:
    return Tenant(id=tenant_id, name=tenant_id, timezone="Europe/Bucharest")


def test_outbox_unique_boundary_is_tenant_kind_key(pg_session):
    pg_session.add_all([_tenant("tenant_a"), _tenant("tenant_b")])
    pg_session.commit()

    enqueue(
        pg_session,
        tenant_id="tenant_a",
        kind=JobKind.NOOP.value,
        idempotency_key="shared-key",
    )
    enqueue(
        pg_session,
        tenant_id="tenant_b",
        kind=JobKind.NOOP.value,
        idempotency_key="shared-key",
    )
    enqueue(
        pg_session,
        tenant_id="tenant_a",
        kind=JobKind.TENANT_BOOTSTRAP.value,
        idempotency_key="shared-key",
    )
    pg_session.commit()
    assert pg_session.query(OutboxJob).filter_by(idempotency_key="shared-key").count() == 3

    with pytest.raises(IntegrityError):
        enqueue(
            pg_session,
            tenant_id="tenant_a",
            kind=JobKind.NOOP.value,
            idempotency_key="shared-key",
        )
    pg_session.rollback()
    assert pg_session.query(OutboxJob).filter_by(idempotency_key="shared-key").count() == 3


def test_concurrent_exact_export_enqueue_converges_on_one_run(
    monkeypatch,
    pg_session,
    pg_session_factory,
):
    pg_session.add(_tenant("tenant_race"))
    pg_session.commit()

    barrier = threading.Barrier(2)
    original_lookup = s5b._existing_export_for_key
    local = threading.local()

    def synchronized_lookup(*args, **kwargs):
        result = original_lookup(*args, **kwargs)
        if result is None and not getattr(local, "initial_lookup_done", False):
            local.initial_lookup_done = True
            barrier.wait(timeout=10)
        return result

    monkeypatch.setattr(s5b, "_existing_export_for_key", synchronized_lookup)

    responses: list[dict[str, object]] = []
    errors: list[BaseException] = []
    result_lock = threading.Lock()

    def submit() -> None:
        session = pg_session_factory()
        try:
            response = s5b._enqueue_export(
                session,
                tenant_id="tenant_race",
                month_id="month_race",
                kind=JobKind.EXPORT_XLSX_STORE,
                idempotency_key="concurrent-export",
                payload={"store_id": "store_race"},
                artifact_uri_hint="/tmp/ugrile-race-artifact.xlsx",
            )
            with result_lock:
                responses.append(response)
        except BaseException as exc:  # pragma: no cover - assertion reports details
            with result_lock:
                errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=submit), threading.Thread(target=submit)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(responses) == 2
    assert {response["job_id"] for response in responses} == {responses[0]["job_id"]}
    assert sorted(bool(response["replayed"]) for response in responses) == [False, True]

    pg_session.expire_all()
    assert (
        pg_session.query(OutboxJob)
        .filter_by(
            tenant_id="tenant_race",
            kind=JobKind.EXPORT_XLSX_STORE.value,
            idempotency_key="concurrent-export",
        )
        .count()
        == 1
    )
    assert (
        pg_session.query(ExportRun)
        .filter_by(tenant_id="tenant_race", kind=JobKind.EXPORT_XLSX_STORE.value)
        .count()
        == 1
    )
