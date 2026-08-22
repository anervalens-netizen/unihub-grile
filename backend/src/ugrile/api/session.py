"""Authenticated principal/capability contract for capability-aware clients."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..api.deps import current_principal
from ..domain.enums import RoleName
from ..services.auth import Principal
from ..services.authorization import capabilities_for

router = APIRouter(tags=["session"])


class SessionOut(BaseModel):
    user_id: str
    tenant_id: str
    role: RoleName
    email: str
    capabilities: list[str]


@router.get("/session", response_model=SessionOut)
def get_session(
    principal: Principal = Depends(current_principal),
) -> SessionOut:
    """Return only server-derived identity and finite application capabilities."""

    return SessionOut(
        user_id=principal.user_id,
        tenant_id=principal.tenant_id,
        role=principal.role,
        email=principal.email,
        capabilities=sorted(capability.value for capability in capabilities_for(principal)),
    )


__all__ = ["router"]
