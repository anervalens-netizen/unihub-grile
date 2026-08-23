"""Normalized future Retail identity/session handoff contract (INT-012).

This module defines the Grile-owned shape a future trusted Retail identity
adapter must produce. It does not validate Retail tokens and it does not mount a
Retail provider. The adapter is responsible for authenticating the host session
and translating it into this finite contract before Grile resolves a local
principal.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from ..domain.enums import RoleName
from ..domain.errors import ScopeError
from .auth import Principal
from .authorization import Capability, capabilities_for

RETAIL_IDENTITY_SCHEMA_V1 = "retail-grile.identity.v1"


class RetailSessionClaimsV1(BaseModel):
    """Normalized, already-authenticated host session claims.

    ``capabilities`` is a ceiling supplied by the host integration. It may
    narrow the Grile role but can never grant a capability the Grile role does
    not already own.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default=RETAIL_IDENTITY_SCHEMA_V1)
    subject: str = Field(min_length=1, max_length=256)
    tenant_id: str = Field(min_length=1, max_length=128)
    role: RoleName
    email: str = Field(min_length=3, max_length=320)
    capabilities: frozenset[Capability]
    session_id: str = Field(min_length=1, max_length=256)
    correlation_id: str | None = Field(default=None, max_length=256)
    issued_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.schema_version != RETAIL_IDENTITY_SCHEMA_V1:
            raise ValueError("unsupported Retail identity schema")
        if self.issued_at.utcoffset() is None or self.expires_at.utcoffset() is None:
            raise ValueError("Retail identity timestamps must be timezone-aware")
        if self.expires_at <= self.issued_at:
            raise ValueError("Retail identity session must expire after it is issued")
        return self


@runtime_checkable
class RetailIdentityAdapter(Protocol):
    """Future host adapter shape; intentionally has no implementation here."""

    name: str

    def resolve_claims(self, *, session_token: str) -> RetailSessionClaimsV1:
        """Authenticate/validate the host session and return normalized claims."""
        ...

    def resolve_principal(
        self,
        session: Session,
        *,
        claims: RetailSessionClaimsV1,
    ) -> Principal:
        """Map the trusted host subject to one existing/approved Grile principal."""
        ...


def apply_retail_capability_ceiling(
    principal: Principal,
    claims: RetailSessionClaimsV1,
) -> Principal:
    """Bind trusted claims to a resolved principal without widening authority.

    Subject-to-user mapping is owned by the future adapter. This helper checks
    the security-critical normalized facts after that mapping: tenant, role and
    finite capabilities. Host over-claims fail closed instead of being silently
    accepted.
    """

    if principal.tenant_id != claims.tenant_id:
        raise ScopeError(
            "Retail identity tenant does not match resolved principal",
            details={
                "principal_tenant": principal.tenant_id,
                "claims_tenant": claims.tenant_id,
            },
        )
    if principal.role is not claims.role:
        raise ScopeError(
            "Retail identity role does not match resolved principal",
            details={
                "principal_role": principal.role.value,
                "claims_role": claims.role.value,
            },
        )

    local = capabilities_for(principal)
    overclaimed = sorted(
        capability.value for capability in claims.capabilities if capability not in local
    )
    if overclaimed:
        raise ScopeError(
            "Retail identity claims capabilities outside the Grile role",
            details={"overclaimed_capabilities": overclaimed},
        )

    return replace(
        principal,
        capability_ceiling=frozenset(capability.value for capability in claims.capabilities),
    )


__all__ = [
    "RETAIL_IDENTITY_SCHEMA_V1",
    "RetailIdentityAdapter",
    "RetailSessionClaimsV1",
    "apply_retail_capability_ceiling",
]
