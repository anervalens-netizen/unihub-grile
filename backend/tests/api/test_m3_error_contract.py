from __future__ import annotations

from ugrile.core import database
from ugrile.domain.enums import MonthState
from ugrile.repositories.months import MonthRepository

ADMIN = {"X-Ugrile-Identity": "user_admin", "X-Ugrile-Tenant": "tenant_acme"}


def _month(faker_tenant, *, state: MonthState, revision: int) -> str:
    with database.session_scope() as session:
        month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
        month.state = state.value
        month.revision = revision
        session.commit()
        return month.id


def _working_cell(faker_tenant) -> dict[str, str]:
    return {
        "person_id": faker_tenant["person_a_id"],
        "business_date": "2026-08-12",
        "status": "WORKING",
        "store_id": faker_tenant["store_id"],
        "working_kind": "NORMAL",
    }


def test_auth_error_uses_canonical_top_level_envelope(client):
    response = client.get("/session")

    assert response.status_code == 401
    assert response.json() == {
        "code": "AUTH_ERROR",
        "message": "missing development identity headers",
        "details": {"provider": "dev_headers"},
    }


def test_month_closed_promotes_semantic_conflict_code(client, faker_tenant):
    month_id = _month(faker_tenant, state=MonthState.CLOSED, revision=7)

    response = client.post(
        f"/months/{month_id}/program/cell?expected_revision=7",
        headers=ADMIN,
        json=_working_cell(faker_tenant),
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": "MONTH_CLOSED",
        "message": "month is closed; reopen before editing",
        "details": {"month_id": month_id},
    }


def test_stale_revision_has_typed_409_payload(client, faker_tenant):
    month_id = _month(faker_tenant, state=MonthState.OPEN, revision=3)

    response = client.post(
        f"/months/{month_id}/program/cell?expected_revision=2",
        headers=ADMIN,
        json=_working_cell(faker_tenant),
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": "STALE_REVISION",
        "message": "stale calendar revision",
        "details": {"expected": 2, "current": 3},
    }


def test_fastapi_http_exception_is_unwrapped(client, faker_tenant):
    month_id = _month(faker_tenant, state=MonthState.CLOSED, revision=9)

    response = client.post(
        f"/months/{month_id}/program/cell?expected_revision=9",
        headers=ADMIN,
        json=_working_cell(faker_tenant),
    )

    assert response.status_code == 409
    assert response.json() == {
        "code": "MONTH_CLOSED",
        "message": "month is closed; reopen before editing",
        "details": {"month_id": month_id},
    }


def test_request_validation_uses_typed_envelope(client):
    response = client.post(
        "/months/irrelevant/program/cell?expected_revision=not-an-int",
        headers=ADMIN,
        json={},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "REQUEST_VALIDATION_ERROR"
    assert body["message"] == "request validation failed"
    assert set(body) == {"code", "message", "details"}
    assert isinstance(body["details"]["errors"], list)
    assert body["details"]["errors"]


def test_framework_404_uses_typed_envelope(client):
    response = client.get("/route-that-does-not-exist")

    assert response.status_code == 404
    assert response.json() == {
        "code": "HTTP_404",
        "message": "Not Found",
        "details": {},
    }
