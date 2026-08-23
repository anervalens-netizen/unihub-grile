from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ugrile.domain.enums import RoleName
from ugrile.domain.errors import ScopeError
from ugrile.services.auth import Principal
from ugrile.services.authorization import Capability, capabilities_for
from ugrile.services.retail_identity import (
    RETAIL_IDENTITY_SCHEMA_V1,
    RetailSessionClaimsV1,
    apply_retail_capability_ceiling,
)


def _claims(**overrides: object) -> RetailSessionClaimsV1:
    payload: dict[str, object] = {
        "schema_version": RETAIL_IDENTITY_SCHEMA_V1,
        "subject": "retail-user-42",
        "tenant_id": "tenant_acme",
        "role": "MANAGER",
        "email": "manager@example.test",
        "capabilities": ["catalog.read", "schedule.read"],
        "session_id": "session-abc",
        "correlation_id": "request-123",
        "issued_at": datetime(2026, 8, 23, 18, 0, tzinfo=UTC),
        "expires_at": datetime(2026, 8, 23, 19, 0, tzinfo=UTC),
    }
    payload.update(overrides)
    return RetailSessionClaimsV1.model_validate(payload)


def _manager_principal() -> Principal:
    return Principal(
        user_id="user_manager",
        tenant_id="tenant_acme",
        role=RoleName.MANAGER,
        email="manager@example.test",
    )


def test_identity_contract_is_versioned_and_rejects_unknown_fields() -> None:
    claims = _claims()
    assert claims.schema_version == RETAIL_IDENTITY_SCHEMA_V1
    assert claims.capabilities == frozenset({Capability.CATALOG_READ, Capability.SCHEDULE_READ})

    with pytest.raises(ValidationError):
        _claims(schema_version="retail-grile.identity.v2")
    with pytest.raises(ValidationError):
        _claims(unexpected="not-allowed")
    with pytest.raises(ValidationError):
        _claims(capabilities=["catalog.read", "future.unknown"])


def test_identity_contract_requires_bounded_timezone_aware_session() -> None:
    with pytest.raises(ValidationError):
        _claims(
            issued_at=datetime(2026, 8, 23, 18, 0),
            expires_at=datetime(2026, 8, 23, 19, 0),
        )
    with pytest.raises(ValidationError):
        _claims(
            issued_at=datetime(2026, 8, 23, 19, 0, tzinfo=UTC),
            expires_at=datetime(2026, 8, 23, 18, 0, tzinfo=UTC),
        )


def test_retail_capabilities_can_narrow_but_not_widen_grile_role() -> None:
    principal = _manager_principal()
    narrowed = apply_retail_capability_ceiling(principal, _claims())

    assert capabilities_for(narrowed) == frozenset(
        {Capability.CATALOG_READ, Capability.SCHEDULE_READ}
    )
    assert Capability.SCHEDULE_WRITE in capabilities_for(principal)
    assert Capability.SCHEDULE_WRITE not in capabilities_for(narrowed)

    with pytest.raises(ScopeError, match="outside the Grile role"):
        apply_retail_capability_ceiling(
            principal,
            _claims(capabilities=["catalog.read", "month.close"]),
        )


def test_retail_claims_must_match_resolved_tenant_and_role() -> None:
    principal = _manager_principal()

    with pytest.raises(ScopeError, match="tenant does not match"):
        apply_retail_capability_ceiling(principal, _claims(tenant_id="tenant_other"))
    with pytest.raises(ScopeError, match="role does not match"):
        apply_retail_capability_ceiling(principal, _claims(role="ADMIN"))
