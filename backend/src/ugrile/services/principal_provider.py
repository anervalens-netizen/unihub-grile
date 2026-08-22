"""Identity-provider seam for standalone development and future Retail mounting.

The domain/API consume :class:`Principal`; they do not depend on a specific SSO
mechanism. The standalone app currently implements only development headers.
A future Retail adapter can implement the same protocol without changing domain
services or route authorization.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.orm import Session

from .auth import Principal, load_principal


class PrincipalProvider(Protocol):
    """Resolve one authenticated application principal from provider inputs."""

    name: str

    def resolve(
        self,
        session: Session,
        *,
        identity: str | None,
        tenant: str | None,
    ) -> Principal:
        ...


class DevHeaderPrincipalProvider:
    """Explicit development/test provider backed by X-Ugrile-* headers."""

    name = "dev_headers"

    def resolve(
        self,
        session: Session,
        *,
        identity: str | None,
        tenant: str | None,
    ) -> Principal:
        if not identity or not tenant:
            from ..domain.errors import AuthError

            raise AuthError(
                "missing development identity headers",
                details={"provider": self.name},
            )
        return load_principal(session, user_id=identity, tenant_id=tenant)


DEV_HEADER_PROVIDER = DevHeaderPrincipalProvider()


__all__ = ["DEV_HEADER_PROVIDER", "DevHeaderPrincipalProvider", "PrincipalProvider"]
