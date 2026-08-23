from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from ugrile.api.health import _dependency_report, _expected_schema_version
from ugrile.repositories.models import OutboxJob, Tenant


def _pin_test_schema_head(pg_session) -> str:
    head = _expected_schema_version()
    pg_session.execute(
        text(
            "CREATE TABLE IF NOT EXISTS alembic_version "
            "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
    )
    pg_session.execute(text("DELETE FROM alembic_version"))
    pg_session.execute(
        text("INSERT INTO alembic_version(version_num) VALUES (:head)"),
        {"head": head},
    )
    pg_session.commit()
    return head


def test_ready_report_is_ok_at_schema_head_with_no_stale_lease(pg_session) -> None:
    head = _pin_test_schema_head(pg_session)

    report = _dependency_report(pg_session)

    assert report.status == "ok"
    assert report.database is True
    assert report.schema_version == head
    assert report.expected_schema_version == head
    assert report.schema_current is True
    assert report.worker_enabled is True
    assert report.stale_running_jobs == 0


def test_ready_report_fails_closed_on_expired_running_lease(pg_session) -> None:
    _pin_test_schema_head(pg_session)
    tenant = Tenant(
        id="tenant_ops_ready",
        name="Ops readiness tenant",
        timezone="Europe/Bucharest",
        is_active=True,
    )
    pg_session.add(tenant)
    pg_session.flush()
    pg_session.add(
        OutboxJob(
            tenant_id=tenant.id,
            kind="NOOP",
            status="RUNNING",
            idempotency_key="stale-ready-probe",
            payload="{}",
            attempts=1,
            run_after=datetime.now(tz=UTC) - timedelta(hours=2),
            locked_at=datetime.now(tz=UTC) - timedelta(hours=2),
            locked_by="dead-worker",
        )
    )
    pg_session.commit()

    report = _dependency_report(pg_session)

    assert report.status == "down"
    assert report.database is True
    assert report.schema_current is True
    assert report.stale_running_jobs == 1
