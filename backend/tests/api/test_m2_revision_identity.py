"""Proof that administrative month revision and calendar data revision are distinct."""

from __future__ import annotations

import json
import os
from datetime import date

from fastapi.testclient import TestClient

from ugrile.core import database
from ugrile.domain.enums import DayStatus, MonthState, WorkingKind
from ugrile.main import create_app
from ugrile.repositories.models import ExportRun
from ugrile.repositories.months import MonthRepository
from ugrile.services.calendar import CalendarChange, CalendarService
from ugrile.worker.worker import WORKER_LOCKED_BY, run_once


def test_export_allows_new_month_revision_with_unchanged_data_revision(engine, faker_tenant):
    """An administrative revision advance must not invent a new data snapshot.

    Close/reopen transitions advance ``Month.revision`` without rebuilding the
    calendar. This fixture models that exact identity split directly so the
    export boundary proves it persists ``m2:d1`` and renders data revision 1.
    """

    artifact: str | None = None
    headers = {
        "X-Ugrile-Identity": "user_admin",
        "X-Ugrile-Tenant": faker_tenant["tenant_id"],
    }
    with TestClient(create_app()) as client:
        with database.session_scope() as session:
            month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
            month.state = MonthState.OPEN
            result = CalendarService(session).apply(
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
            assert result.revision == 1
            month_id = month.id
            session.commit()

        # Model an administrative close/reopen-style revision advance: the
        # Month CAS identity changes, while assignments/Pontaj remain revision 1.
        with database.session_scope() as session:
            month = MonthRepository(session).get(month_id)
            month.revision = 2
            session.commit()

        response = client.post(
            f"/months/{month_id}/export/store",
            json={"store_id": faker_tenant["store_id"]},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["month_revision"] == 2
        assert body["data_revision"] == 1
        assert body["idempotency_key"].endswith("::m2::d1")

        row, result = run_once(locked_by=WORKER_LOCKED_BY)
        assert row is not None and row.status == "DONE"
        assert result is not None and result.status == "DONE"

        with database.session_scope() as session:
            export_run = session.get(ExportRun, body["job_id"])
            assert export_run is not None and export_run.status == "DONE"
            summary = json.loads(export_run.summary)
            assert summary["summary"]["revision"] == 1
            artifact = export_run.artifact_uri
            assert artifact is not None and os.path.exists(artifact)

    if artifact and os.path.exists(artifact):
        os.unlink(artifact)
