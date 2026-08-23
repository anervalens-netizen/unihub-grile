"""Read-only catalog endpoints (tenants, stores, people).

These endpoints power the manager UI shell and the E-pay audit. Writes are
out of scope at S1 — they arrive in S2 (calendar) and S5 (Sheet binding).
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..api.deps import current_principal, db_session
from ..api.schemas import PersonOut, StoreOut, TenantOut
from ..domain.errors import ScopeError
from ..repositories.stores import PersonRepository, StoreRepository
from ..repositories.tenants import TenantRepository
from ..services.auth import (
    Principal,
    assert_admin,
    assert_same_tenant,
    effective_store_ids,
)
from ..services.authorization import Capability, authorize

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/tenants", response_model=list[TenantOut])
def list_tenants(
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[TenantOut]:
    assert_admin(principal)
    return [TenantOut.model_validate(t) for t in TenantRepository(session).list()]


@router.get("/stores", response_model=list[StoreOut])
def list_stores(
    tenant_id: str | None = None,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[StoreOut]:
    authorize(principal, Capability.CATALOG_READ)
    if tenant_id is not None:
        assert_same_tenant(principal, tenant_id)
    else:
        tenant_id = principal.tenant_id
    allowed_store_ids = effective_store_ids(session, principal, date.today())
    return [
        StoreOut.model_validate(s)
        for s in StoreRepository(session).list_for_tenant(tenant_id)
        if s.id in allowed_store_ids
    ]


@router.get("/people", response_model=list[PersonOut])
def list_people(
    tenant_id: str | None = None,
    store_id: str | None = None,
    session: Session = Depends(db_session),
    principal: Principal = Depends(current_principal),
) -> list[PersonOut]:
    authorize(principal, Capability.CATALOG_READ)
    if tenant_id is not None:
        assert_same_tenant(principal, tenant_id)
    else:
        tenant_id = principal.tenant_id
    repo = PersonRepository(session)
    allowed_store_ids = effective_store_ids(session, principal, date.today())
    if store_id is not None:
        if store_id not in allowed_store_ids:
            raise ScopeError("store is outside manager scope", details={"store_id": store_id})
        return [
            PersonOut.model_validate(p)
            for p in repo.list_for_store(tenant_id=tenant_id, store_id=store_id)
        ]
    return [
        PersonOut.model_validate(p)
        for p in repo.list_for_tenant(tenant_id=tenant_id)
        if p.home_store_id in allowed_store_ids
    ]


__all__ = ["router"]
