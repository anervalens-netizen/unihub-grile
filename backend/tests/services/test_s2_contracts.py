"""Single-use schedule import contract: issue, validate, consume, tamper."""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

import pytest

from ugrile.domain.enums import MonthState
from ugrile.domain.errors import ConflictError, StaleRevisionError, ValidationError
from ugrile.repositories.models import ScheduleImportContract
from ugrile.repositories.months import MonthRepository
from ugrile.services.schedule_contract import (
    consume_contract,
    issue_contract,
    validate_contract,
)


def _catalog(faker_tenant) -> tuple[dict[str, str], list[dict[str, str]]]:
    stores = {
        "s1": faker_tenant["store_id"],
        "s2": faker_tenant["other_store_id"],
    }
    people = [
        {
            "person_id": faker_tenant["person_a_id"],
            "display_name": "Alice",
            "home_store_code": "s1",
            "manager_code": "m1",
        },
        {
            "person_id": faker_tenant["person_c_id"],
            "display_name": "Carmen",
            "home_store_code": "s2",
            "manager_code": "m1",
        },
    ]
    return stores, people


def _full_scope(faker_tenant) -> dict[date, set[str]]:
    return {
        date(2026, 8, d): {faker_tenant["store_id"], faker_tenant["other_store_id"]}
        for d in range(1, 32)
    }


def _issue(session, faker_tenant, **overrides) -> tuple[str, dict[str, object]]:
    month = MonthRepository(session).get_or_create(faker_tenant["tenant_id"], 2026, 8)
    month.state = MonthState.OPEN
    stores, people = _catalog(faker_tenant)
    kwargs: dict[str, object] = dict(
        tenant_id=faker_tenant["tenant_id"],
        month=month,
        user_id="user_admin",
        base_revision=0,
        stores=stores,
        people=people,
        allowed_by_date=_full_scope(faker_tenant),
    )
    kwargs.update(overrides)
    token = issue_contract(session, **kwargs)  # type: ignore[arg-type]
    return token, kwargs


def _validate(session, faker_tenant, token, kwargs, **overrides) -> ScheduleImportContract:
    validate_kwargs: dict[str, object] = dict(
        token=token,
        tenant_id=faker_tenant["tenant_id"],
        month=kwargs["month"],
        user_id="user_admin",
        parsed_tenant_id=faker_tenant["tenant_id"],
        parsed_month_id=kwargs["month"].id,
        parsed_revision=0,
        stores=kwargs["stores"],
        people=kwargs["people"],
        allowed_by_date=kwargs["allowed_by_date"],
        lock=False,
    )
    validate_kwargs.update(overrides)
    return validate_contract(session, **validate_kwargs)  # type: ignore[arg-type]


def test_contract_lifecycle_issue_validate_consume_replay(session, faker_tenant):
    token, kwargs = _issue(session, faker_tenant)
    contract = _validate(session, faker_tenant, token, kwargs)
    assert contract.consumed_at is None
    consume_contract(session, contract)
    with pytest.raises(ConflictError) as exc:
        _validate(session, faker_tenant, token, kwargs)
    assert exc.value.details["code"] == "CONTRACT_CONSUMED"


def test_contract_locked_validation_does_not_consume(session, faker_tenant):
    token, kwargs = _issue(session, faker_tenant)
    _validate(session, faker_tenant, token, kwargs, lock=True)
    session.flush()
    contract = _validate(session, faker_tenant, token, kwargs)
    assert contract.consumed_at is None


def test_contract_token_is_stored_only_as_sha256(session, faker_tenant):
    token, _ = _issue(session, faker_tenant)
    row = session.query(ScheduleImportContract).one()
    assert row.token_hash != token
    assert row.token_hash == hashlib.sha256(token.encode("utf-8")).hexdigest()


def test_contract_rejects_wrong_token(session, faker_tenant):
    token, kwargs = _issue(session, faker_tenant)
    with pytest.raises(ValidationError) as exc:
        _validate(session, faker_tenant, "totally-different-token", kwargs)
    assert exc.value.details["code"] == "MANIFEST_TAMPERED"


def test_contract_rejects_wrong_user(session, faker_tenant):
    token, kwargs = _issue(session, faker_tenant)
    with pytest.raises(ValidationError) as exc:
        _validate(session, faker_tenant, token, kwargs, user_id="user_other")
    assert exc.value.details["code"] == "MANIFEST_TAMPERED"


def test_contract_rejects_wrong_tenant(session, faker_tenant):
    token, kwargs = _issue(session, faker_tenant)
    with pytest.raises(ValidationError) as exc:
        _validate(
            session,
            faker_tenant,
            token,
            kwargs,
            tenant_id="tenant_other",
            parsed_tenant_id="tenant_other",
        )
    assert exc.value.details["code"] == "MANIFEST_TAMPERED"


def test_contract_rejects_wrong_month_in_manifest(session, faker_tenant):
    token, kwargs = _issue(session, faker_tenant)
    with pytest.raises(ValidationError) as exc:
        _validate(session, faker_tenant, token, kwargs, parsed_month_id="month_other")
    assert exc.value.details["code"] == "MANIFEST_TAMPERED"


def test_contract_rejects_tampered_base_revision(session, faker_tenant):
    token, kwargs = _issue(session, faker_tenant)
    with pytest.raises(ValidationError) as exc:
        _validate(session, faker_tenant, token, kwargs, parsed_revision=7)
    assert exc.value.details["code"] == "MANIFEST_TAMPERED"


def test_contract_rejects_stale_month_revision(session, faker_tenant):
    token, kwargs = _issue(session, faker_tenant)
    month = kwargs["month"]
    month.revision = 1  # another apply advanced the calendar
    session.flush()
    with pytest.raises(StaleRevisionError) as exc:
        _validate(session, faker_tenant, token, kwargs)
    assert exc.value.details["expected"] == 0
    assert exc.value.details["current"] == 1


def test_contract_rejects_changed_catalog(session, faker_tenant):
    token, kwargs = _issue(session, faker_tenant)
    stores, _ = _catalog(faker_tenant)
    stores["s3"] = "store_acme_s3"  # a store appeared after issuance
    with pytest.raises(ValidationError) as exc:
        _validate(session, faker_tenant, token, kwargs, stores=stores)
    assert exc.value.details["code"] == "CONTRACT_CHANGED"


def test_contract_rejects_changed_scope(session, faker_tenant):
    token, kwargs = _issue(session, faker_tenant)
    narrowed = {date(2026, 8, d): {faker_tenant["store_id"]} for d in range(1, 32)}
    with pytest.raises(ValidationError) as exc:
        _validate(session, faker_tenant, token, kwargs, allowed_by_date=narrowed)
    assert exc.value.details["code"] == "CONTRACT_CHANGED"


def test_contract_rejects_expired_token(session, faker_tenant):
    token, kwargs = _issue(session, faker_tenant)
    row = session.query(ScheduleImportContract).one()
    row.expires_at = row.issued_at - timedelta(minutes=1)
    session.flush()
    with pytest.raises(ValidationError) as exc:
        _validate(session, faker_tenant, token, kwargs)
    assert exc.value.details["code"] == "CONTRACT_EXPIRED"


def test_contract_hash_digests_are_deterministic(faker_tenant):
    from ugrile.services.schedule_contract import catalog_hash, scope_hash

    stores, people = _catalog(faker_tenant)
    scope = _full_scope(faker_tenant)
    first_catalog = catalog_hash(stores, people)
    first_scope = scope_hash(scope)
    assert first_catalog == catalog_hash(stores, people)
    assert first_scope == scope_hash(scope)
    assert first_catalog != catalog_hash(dict(stores, s3="store_acme_s3"), people)
    assert first_scope != scope_hash(
        {date(2026, 8, d): {faker_tenant["store_id"]} for d in range(1, 32)}
    )
