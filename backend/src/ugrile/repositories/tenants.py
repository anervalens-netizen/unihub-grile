"""Tenant repository.

Tiny at S1; the foundation is responsible only for the bootstrap tenant and a
fixture-friendly ``upsert`` helper. Authentication state is intentionally out
of scope at S1; later stages will integrate the SSO/Tailscale-issued identity.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.errors import NotFoundError
from .models import Tenant


class TenantRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, tenant_id: str) -> Tenant:
        tenant = self.session.get(Tenant, tenant_id)
        if tenant is None:
            raise NotFoundError(f"tenant not found: {tenant_id}")
        return tenant

    def find(self, tenant_id: str) -> Tenant | None:
        return self.session.get(Tenant, tenant_id)

    def list(self) -> list[Tenant]:
        return list(self.session.execute(select(Tenant).order_by(Tenant.id)).scalars())

    def upsert(
        self,
        *,
        tenant_id: str,
        name: str,
        timezone: str,
        is_active: bool = True,
    ) -> Tenant:
        existing = self.session.get(Tenant, tenant_id)
        if existing is None:
            tenant = Tenant(id=tenant_id, name=name, timezone=timezone, is_active=is_active)
            self.session.add(tenant)
            return tenant
        existing.name = name
        existing.timezone = timezone
        existing.is_active = is_active
        return existing


__all__ = ["TenantRepository"]
