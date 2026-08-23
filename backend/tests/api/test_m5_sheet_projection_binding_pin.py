"""GS-003 API/worker proofs for binding-bound Google projection jobs."""

from __future__ import annotations

import json

from ugrile.connectors.google import fake_spreadsheet_id
from ugrile.core import database
from ugrile.repositories.models import OutboxJob, SheetBinding, SheetProjectionRun
from ugrile.repositories.months import MonthRepository
from ugrile.worker.worker import run_once

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}


def _month_id(faker_tenant) -> str:
    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(
            faker_tenant["tenant_id"],
            2026,
            8,
        )
        return month.id


def _bind(client, store_id: str, spreadsheet_id: str, *, expected: str | None = None) -> None:
    body: dict[str, str] = {
        "spreadsheet_id": spreadsheet_id,
        "sheet_name_grila": "Grila",
        "sheet_name_pontaj": "Pontaj",
    }
    if expected is not None:
        body["expected_current_spreadsheet_id"] = expected
        body["expected_current_sheet_name_grila"] = "Grila"
        body["expected_current_sheet_name_pontaj"] = "Pontaj"
        body["reason"] = "replace workbook"
    response = client.put(f"/sheet-bindings/{store_id}", headers=ADMIN, json=body)
    assert response.status_code == 200, response.text


def _job_payload(job_id: int) -> dict[str, object]:
    with database.session_scope() as session:
        row = session.get(OutboxJob, job_id)
        assert row is not None
        payload = json.loads(row.payload)
        assert isinstance(payload, dict)
        return payload


def test_fake_enqueue_pins_deterministic_identity_before_binding_exists(
    client,
    faker_tenant,
) -> None:
    month_id = _month_id(faker_tenant)
    response = client.post(
        f"/months/{month_id}/sheet-projection/enqueue",
        headers=ADMIN,
        json={"store_id": faker_tenant["store_id"]},
    )

    assert response.status_code == 200, response.text
    payload = _job_payload(response.json()["id"])
    assert payload["binding_spreadsheet_id"] == fake_spreadsheet_id(
        faker_tenant["tenant_id"], faker_tenant["store_id"]
    )
    assert payload["binding_sheet_name_grila"] == "Grila"
    assert payload["binding_sheet_name_pontaj"] == "Pontaj"
    assert len(response.json()["idempotency_key"]) <= 128


def test_default_idempotency_identity_changes_after_rebind(client, faker_tenant) -> None:
    month_id = _month_id(faker_tenant)
    store_id = faker_tenant["store_id"]
    _bind(client, store_id, "sheet-v1")

    first = client.post(
        f"/months/{month_id}/sheet-projection/enqueue",
        headers=ADMIN,
        json={"store_id": store_id},
    )
    assert first.status_code == 200, first.text

    _bind(client, store_id, "sheet-v2", expected="sheet-v1")
    second = client.post(
        f"/months/{month_id}/sheet-projection/enqueue",
        headers=ADMIN,
        json={"store_id": store_id},
    )

    assert second.status_code == 200, second.text
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["idempotency_key"] != second.json()["idempotency_key"]
    assert _job_payload(first.json()["id"])["binding_spreadsheet_id"] == "sheet-v1"
    assert _job_payload(second.json()["id"])["binding_spreadsheet_id"] == "sheet-v2"


def test_custom_idempotency_key_reuse_after_rebind_fails_closed(client, faker_tenant) -> None:
    month_id = _month_id(faker_tenant)
    store_id = faker_tenant["store_id"]
    _bind(client, store_id, "sheet-v1")
    request = {"store_id": store_id, "idempotency_key": "operator-sync-42"}

    first = client.post(
        f"/months/{month_id}/sheet-projection/enqueue",
        headers=ADMIN,
        json=request,
    )
    assert first.status_code == 200, first.text

    _bind(client, store_id, "sheet-v2", expected="sheet-v1")
    replay = client.post(
        f"/months/{month_id}/sheet-projection/enqueue",
        headers=ADMIN,
        json=request,
    )

    assert replay.status_code == 409, replay.text
    assert replay.json()["details"]["code"] == "SHEET_IDEMPOTENCY_KEY_REUSED"


def test_rebind_after_enqueue_makes_old_job_terminal_before_projection(client, faker_tenant) -> None:
    month_id = _month_id(faker_tenant)
    store_id = faker_tenant["store_id"]
    _bind(client, store_id, "sheet-v1")
    queued = client.post(
        f"/months/{month_id}/sheet-projection/enqueue",
        headers=ADMIN,
        json={"store_id": store_id},
    )
    assert queued.status_code == 200, queued.text
    job_id = queued.json()["id"]

    _bind(client, store_id, "sheet-v2", expected="sheet-v1")
    row, result = run_once(locked_by="gs003-test-worker")

    assert row is not None
    assert row.id == job_id
    assert result is None
    assert row.status == "FAILED"
    assert row.attempts == 1
    assert row.last_error is not None
    assert "TERMINAL" in row.last_error
    assert "GOOGLE_SHEET_BINDING_STALE" in row.last_error

    with database.session_scope() as session:
        binding = (
            session.query(SheetBinding)
            .filter_by(tenant_id=faker_tenant["tenant_id"], store_id=store_id)
            .one()
        )
        assert binding.spreadsheet_id == "sheet-v2"
        assert binding.generation == "UNPROJECTED"
        assert (
            session.query(SheetProjectionRun)
            .filter_by(tenant_id=faker_tenant["tenant_id"], store_id=store_id)
            .count()
            == 0
        )


def test_legacy_projection_job_without_binding_pin_fails_terminal(client, faker_tenant) -> None:
    month_id = _month_id(faker_tenant)
    store_id = faker_tenant["store_id"]
    with database.session_scope() as session:
        month = MonthRepository(session).get(month_id)
        row = OutboxJob(
            tenant_id=faker_tenant["tenant_id"],
            kind="GOOGLE_PROJECTION_STORE",
            status="PENDING",
            idempotency_key="legacy-unpinned",
            payload=json.dumps(
                {
                    "month_id": month.id,
                    "store_id": store_id,
                    "year": month.year,
                    "month": month.month,
                    "month_revision": month.revision,
                    "revision": month.revision,
                }
            ),
        )
        session.add(row)
        session.flush()
        job_id = row.id

    row, result = run_once(locked_by="gs003-legacy-test-worker")

    assert row is not None
    assert row.id == job_id
    assert result is None
    assert row.status == "FAILED"
    assert row.attempts == 1
    assert row.last_error is not None
    assert "TERMINAL" in row.last_error
    assert "JOB_METADATA_MISSING" in row.last_error
