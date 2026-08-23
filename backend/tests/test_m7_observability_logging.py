from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from ugrile.core.logging import pii_safe_event, safe_exception_fields
from ugrile.domain.errors import DomainError
from ugrile.repositories.models import OutboxJob
from ugrile.worker import worker as worker_module


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, object]]] = []

    def info(self, event: str, **fields: object) -> None:
        self.events.append(("info", event, fields))

    def warning(self, event: str, **fields: object) -> None:
        self.events.append(("warning", event, fields))

    def error(self, event: str, **fields: object) -> None:
        self.events.append(("error", event, fields))


def _event_text(events: list[tuple[str, str, dict[str, object]]]) -> str:
    return repr(events)


def test_pii_safe_event_drops_unknown_sensitive_and_non_scalar_fields() -> None:
    marker = "Alice Salary 9999"
    raw: dict[str, Any] = {
        "event": "sample",
        "level": "info",
        "timestamp": "2026-08-23T18:00:00Z",
        "correlation_id": "req_safe_123",
        "method": "POST",
        "route": "/months/{month_id}/program/cell",
        "status_code": 200,
        "duration_ms": 12.5,
        "email": "alice@example.test",
        "person_id": "person_alice",
        "salary": 9999,
        "amount": 9999,
        "payload": {"name": marker, "salary": 9999},
        "authorization": "Bearer top-secret",
        "error": marker,
        "details": {"person": marker},
    }

    sanitized = pii_safe_event(None, "info", raw)

    assert sanitized == {
        "event": "sample",
        "level": "info",
        "timestamp": "2026-08-23T18:00:00Z",
        "correlation_id": "req_safe_123",
        "method": "POST",
        "route": "/months/{month_id}/program/cell",
        "status_code": 200,
        "duration_ms": 12.5,
    }
    assert marker not in repr(sanitized)
    assert "top-secret" not in repr(sanitized)

    # Even an allowlisted key is dropped if a caller tries to smuggle a
    # structured object through it instead of an operational scalar.
    assert "route" not in pii_safe_event(None, "info", {"event": "x", "route": {"pii": marker}})


def test_pii_safe_event_bounds_strings_and_exception_fields_omit_messages() -> None:
    long_correlation = "x" * 500
    sanitized = pii_safe_event(
        None,
        "info",
        {"event": "x", "correlation_id": long_correlation},
    )
    assert len(str(sanitized["correlation_id"])) == 256

    runtime_fields = safe_exception_fields(RuntimeError("Alice salary=9999"))
    assert runtime_fields == {"error_type": "RuntimeError"}

    domain_fields = safe_exception_fields(
        DomainError(
            "Alice salary=9999",
            details={"code": "PAYROLL_BLOCKED", "person": "Alice", "salary": 9999},
        )
    )
    assert domain_fields == {
        "error_type": "DomainError",
        "error_code": "PAYROLL_BLOCKED",
    }
    assert "Alice" not in repr(domain_fields)
    assert "9999" not in repr(domain_fields)


def test_api_request_logging_uses_correlation_and_route_template_without_url_data(
    monkeypatch,
) -> None:
    import ugrile.main as main_module

    recorder = _Recorder()
    monkeypatch.setattr(main_module, "log", recorder)
    app = main_module.create_app()

    with TestClient(app) as client:
        response = client.get(
            "/version?email=alice@example.test&salary=9999",
            headers={"X-Correlation-ID": "ops_test_123"},
        )
        assert response.status_code == 200
        assert response.headers["X-Correlation-ID"] == "ops_test_123"

        missing = client.get("/Alice-Salary-9999?authorization=top-secret")
        assert missing.status_code == 404

    completed = [event for event in recorder.events if event[1] == "http_request_completed"]
    assert len(completed) == 2

    _, _, version_fields = completed[0]
    assert version_fields["correlation_id"] == "ops_test_123"
    assert version_fields["method"] == "GET"
    assert version_fields["route"] == "/version"
    assert version_fields["status_code"] == 200
    assert isinstance(version_fields["duration_ms"], float)

    _, _, missing_fields = completed[1]
    assert missing_fields["route"] == "unmatched"

    emitted = _event_text(recorder.events)
    assert "alice@example.test" not in emitted
    assert "Alice-Salary-9999" not in emitted
    assert "top-secret" not in emitted
    assert "salary=9999" not in emitted


def test_unhandled_api_error_returns_generic_envelope_and_safe_log(monkeypatch) -> None:
    import ugrile.main as main_module

    recorder = _Recorder()
    monkeypatch.setattr(main_module, "log", recorder)
    app = main_module.create_app()

    @app.get("/ops-001-boom")
    def _boom() -> None:
        raise RuntimeError("Alice salary=9999")

    with TestClient(app) as client:
        response = client.get(
            "/ops-001-boom?authorization=top-secret",
            headers={"X-Correlation-ID": "ops_boom_123"},
        )

    assert response.status_code == 500
    assert response.headers["X-Correlation-ID"] == "ops_boom_123"
    assert response.json() == {
        "code": "INTERNAL_ERROR",
        "message": "internal server error",
        "details": {},
    }
    failed = [event for event in recorder.events if event[1] == "http_request_failed"]
    assert failed == [
        (
            "error",
            "http_request_failed",
            {
                "correlation_id": "ops_boom_123",
                "method": "GET",
                "route": "/ops-001-boom",
                "status_code": 500,
                "duration_ms": failed[0][2]["duration_ms"],
                "error_type": "RuntimeError",
            },
        )
    ]
    emitted = _event_text(recorder.events) + response.text
    assert "Alice" not in emitted
    assert "9999" not in emitted
    assert "top-secret" not in emitted


def test_worker_failure_log_keeps_type_not_exception_message(monkeypatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(worker_module, "log", recorder)
    now = datetime(2026, 8, 23, 18, 0, tzinfo=UTC)
    row = OutboxJob(
        tenant_id="tenant_test",
        kind="NOOP",
        status="RUNNING",
        idempotency_key="ops-001",
        payload="{}",
        attempts=1,
        run_after=now,
    )

    worker_module._settle_failure(row, RuntimeError("Alice salary=9999"), now=now)

    assert row.status == "FAILED"
    assert "Alice salary=9999" in (row.last_error or "")
    assert recorder.events == [
        (
            "warning",
            "job_failed",
            {
                "kind": "NOOP",
                "attempts": 1,
                "max_attempts": 1,
                "retryable": False,
                "error_type": "RuntimeError",
            },
        )
    ]
    emitted = _event_text(recorder.events)
    assert "Alice" not in emitted
    assert "9999" not in emitted
