"""S5b export + canary API tests."""

from __future__ import annotations

import os
from datetime import date

from fastapi.testclient import TestClient

from ugrile.core import database
from ugrile.domain.enums import DayStatus, MonthState, RoleName, WorkingKind
from ugrile.main import create_app
from ugrile.repositories.months import MonthRepository
from ugrile.services.auth import Principal
from ugrile.services.calendar import CalendarChange, CalendarService


def _build_admin(faker_tenant) -> Principal:
    return Principal(
        user_id="user_admin",
        tenant_id=faker_tenant["tenant_id"],
        role=RoleName.ADMIN,
        email="admin@acme.example",
    )


def _build_client(engine, faker_tenant):
    with TestClient(create_app()) as test_client:
        with database.session_scope() as session:
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
            month_id = month.id
        yield test_client, month_id


def test_export_store_returns_envelope_and_writes_artifact(engine, faker_tenant):
    admin = _build_admin(faker_tenant)
    with TestClient(create_app()) as test_client:
        with database.session_scope() as session:
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
        assert data["filename"].endswith(".xlsx")
        assert data["checksum_sha256"]
        assert os.path.exists(data["artifact_uri"])
        os.unlink(data["artifact_uri"])


def test_export_bulk_returns_zipped_envelope(engine, faker_tenant):
    admin = _build_admin(faker_tenant)
    with TestClient(create_app()) as test_client:
        with database.session_scope() as session:
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
            month_id = month.id
        response = test_client.post(
            f"/months/{month_id}/export/bulk",
            json={"idempotency_key": "xlsx-bulk-1"},
            headers={"X-Ugrile-Identity": admin.user_id, "X-Ugrile-Tenant": admin.tenant_id},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["kind"] == "EXPORT_XLSX_BULK"
        assert data["filename"].endswith(".zip")
        assert data["summary"]["store_count"] >= 1
        os.unlink(data["artifact_uri"])


def test_export_pontaj_only_returns_envelope(engine, faker_tenant):
    admin = _build_admin(faker_tenant)
    with TestClient(create_app()) as test_client:
        with database.session_scope() as session:
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
            month_id = month.id
        response = test_client.post(
            f"/months/{month_id}/export/pontaj-only",
            json={"idempotency_key": "pontaj-1"},
            headers={"X-Ugrile-Identity": admin.user_id, "X-Ugrile-Tenant": admin.tenant_id},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["kind"] == "EXPORT_PONTAJ_ONLY"
        assert data["filename"].endswith(".xlsx")
        os.unlink(data["artifact_uri"])
