"""Adversarial revision-bound async job tests (JOB-006/JOB-008/JOB-011)."""

from __future__ import annotations

import json
import os
from datetime import date

from fastapi.testclient import TestClient

from ugrile.core import database
from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.main import create_app
from ugrile.repositories.models import ExportRun, OutboxJob, SheetProjectionRun
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.worker.worker import WORKER_LOCKED_BY, run_once

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}


def _seed_revision_one(session, tenant: dict[str, str]):
    month = MonthRepository(session).get_or_create(tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    result = CalendarService(session).apply(
        month=month,
        tenant_id=tenant["tenant_id"],
        expected_revision=month.revision,
        changes=[
            CalendarChange(
                tenant["person_a_id"],
                date(2026, 8, 1),
                tenant["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    session.commit()
    assert result.revision == 1
    return month


def _advance_to_revision_two(session, tenant: dict[str, str], month_id: str) -> None:
    month = MonthRepository(session).get(month_id)
    result = CalendarService(session).apply(
        month=month,
        tenant_id=tenant["tenant_id"],
        expected_revision=month.revision,
        changes=[
            CalendarChange(
                tenant["person_a_id"],
                date(2026, 8, 2),
                tenant["store_id"],
                DayStatus.WORKING,
                WorkingKind.NORMAL,
            )
        ],
    )
    session.commit()
    assert result.revision == 2


def _post_store_export(client: TestClient, month_id: str, store_id: str, key: str | None = None):
    body = {"store_id": store_id}
    if key is not None:
        body["idempotency_key"] = key
    return client.post(
        f"/months/{month_id}/export/store",
        json=body,
        headers=ADMIN,
    )


def _post_projection(client: TestClient, month_id: str, store_id: str, key: str | None = None):
    body = {"store_id": store_id}
    if key is not None:
        body["idempotency_key"] = key
    return client.post(
        f"/months/{month_id}/sheet-projection/enqueue",
        json=body,
        headers=ADMIN,
    )


def _cleanup(path: str | None) -> None:
    if path and os.path.exists(path):
        os.unlink(path)


def test_default_export_identity_advances_and_stale_job_never_writes(engine, faker_tenant):
    """R1 queued → calendar R2 makes R1 terminal; R2 gets a new identity."""

    current_artifact: str | None = None
    with TestClient(create_app()) as client:
        with database.session_scope() as session:
            month = _seed_revision_one(session, faker_tenant)
            month_id = month.id

        old_response = _post_store_export(
            client,
            month_id,
            faker_tenant["store_id"],
        )
        assert old_response.status_code == 200, old_response.text
        old = old_response.json()
        assert old["month_revision"] == old["data_revision"] == 1
        assert old["idempotency_key"].endswith("::m1::d1")

        with database.session_scope() as session:
            _advance_to_revision_two(session, faker_tenant, month_id)

        new_response = _post_store_export(
            client,
            month_id,
            faker_tenant["store_id"],
        )
        assert new_response.status_code == 200, new_response.text
        new = new_response.json()
        assert new["month_revision"] == new["data_revision"] == 2
        assert new["idempotency_key"].endswith("::m2::d2")
        assert new["idempotency_key"] != old["idempotency_key"]
        assert new["job_id"] != old["job_id"]

        stale_row, stale_result = run_once(locked_by=WORKER_LOCKED_BY)
        assert stale_row is not None
        assert stale_row.status == "FAILED"
        assert stale_result is None
        assert stale_row.attempts == 1
        assert stale_row.last_error is not None
        assert "JOB_MONTH_REVISION_STALE" in stale_row.last_error
        assert not os.path.exists(old["artifact_uri_hint"])

        with database.session_scope() as session:
            stale_run = session.get(ExportRun, old["job_id"])
            assert stale_run is not None
            assert stale_run.status == "FAILED"
            assert stale_run.artifact_uri is None
            stale_summary = json.loads(stale_run.summary)
            assert "obsolete because the month revision advanced" in stale_summary["errors"]

        current_row, current_result = run_once(locked_by=WORKER_LOCKED_BY)
        assert current_row is not None and current_row.status == "DONE"
        assert current_result is not None and current_result.status == "DONE"
        current_artifact = new["artifact_uri_hint"]
        assert os.path.exists(current_artifact)

        with database.session_scope() as session:
            current_run = session.get(ExportRun, new["job_id"])
            assert current_run is not None and current_run.status == "DONE"
            summary = json.loads(current_run.summary)
            # CalendarService keeps revision 1 Pontaj history. The pinned
            # renderer must select only the 31 rows for revision 2, not both
            # historical revisions (62 rows).
            assert summary["summary"]["revision"] == 2
            assert summary["summary"]["rows_pontaj"] == 31

    _cleanup(current_artifact)


def test_custom_export_key_cannot_replay_across_revision_change(engine, faker_tenant):
    with TestClient(create_app()) as client:
        with database.session_scope() as session:
            month = _seed_revision_one(session, faker_tenant)
            month_id = month.id

        first = _post_store_export(
            client,
            month_id,
            faker_tenant["store_id"],
            key="operator-export-key",
        )
        assert first.status_code == 200, first.text

        with database.session_scope() as session:
            _advance_to_revision_two(session, faker_tenant, month_id)

        conflict = _post_store_export(
            client,
            month_id,
            faker_tenant["store_id"],
            key="operator-export-key",
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["details"]["code"] == "EXPORT_IDEMPOTENCY_KEY_REUSED"
        with database.session_scope() as session:
            assert session.query(ExportRun).count() == 1
            assert session.query(OutboxJob).count() == 1


def test_sheet_projection_replay_is_exact_and_old_revision_is_superseded(engine, faker_tenant):
    """Google projection gets the same revision identity and stale-job guard."""

    with TestClient(create_app()) as client:
        with database.session_scope() as session:
            month = _seed_revision_one(session, faker_tenant)
            month_id = month.id

        first = _post_projection(client, month_id, faker_tenant["store_id"])
        replay = _post_projection(client, month_id, faker_tenant["store_id"])
        assert first.status_code == 200, first.text
        assert replay.status_code == 200, replay.text
        first_body = first.json()
        replay_body = replay.json()
        assert first_body["id"] == replay_body["id"]
        assert first_body["idempotency_key"].endswith(":m1:d1")
        with database.session_scope() as session:
            assert (
                session.query(OutboxJob)
                .filter_by(kind="GOOGLE_PROJECTION_STORE")
                .count()
                == 1
            )

        with database.session_scope() as session:
            _advance_to_revision_two(session, faker_tenant, month_id)

        stale_row, stale_result = run_once(locked_by=WORKER_LOCKED_BY)
        assert stale_row is not None and stale_row.status == "FAILED"
        assert stale_result is None
        assert stale_row.attempts == 1
        assert stale_row.last_error is not None
        assert "JOB_MONTH_REVISION_STALE" in stale_row.last_error
        with database.session_scope() as session:
            assert session.query(SheetProjectionRun).count() == 0

        second = _post_projection(client, month_id, faker_tenant["store_id"])
        assert second.status_code == 200, second.text
        second_body = second.json()
        assert second_body["id"] != first_body["id"]
        assert second_body["idempotency_key"].endswith(":m2:d2")

        current_row, current_result = run_once(locked_by=WORKER_LOCKED_BY)
        assert current_row is not None and current_row.status == "DONE"
        assert current_result is not None and current_result.status == "DONE"
        assert current_result.payload["revision"] == 2

        projection = client.get(
            f"/months/{month_id}/sheet-projection",
            params={"store_id": faker_tenant["store_id"]},
            headers=ADMIN,
        )
        assert projection.status_code == 200, projection.text
        projection_body = projection.json()
        assert projection_body["payload"]["grila"]["revision"] == 2
        assert projection_body["payload"]["pontaj"]["revision"] == 2


def test_sheet_projection_custom_key_reuse_after_revision_change_is_conflict(
    engine,
    faker_tenant,
):
    with TestClient(create_app()) as client:
        with database.session_scope() as session:
            month = _seed_revision_one(session, faker_tenant)
            month_id = month.id

        first = _post_projection(
            client,
            month_id,
            faker_tenant["store_id"],
            key="sheet-operator-key",
        )
        assert first.status_code == 200, first.text

        with database.session_scope() as session:
            _advance_to_revision_two(session, faker_tenant, month_id)

        conflict = _post_projection(
            client,
            month_id,
            faker_tenant["store_id"],
            key="sheet-operator-key",
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["details"]["code"] == "SHEET_IDEMPOTENCY_KEY_REUSED"
        with database.session_scope() as session:
            assert (
                session.query(OutboxJob)
                .filter_by(kind="GOOGLE_PROJECTION_STORE")
                .count()
                == 1
            )
