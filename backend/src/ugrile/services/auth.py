"""Auth and manager scopes skeleton.

S1 only needs to prove the seam where identity and effective-dated scopes
plug in. Real authentication is delegated to the host (DSH session); this
module accepts an opaque ``X-Ugrile-Identity`` header in dev/test and
verifies that the principal has a non-empty role and an existing tenant.

The skeleton avoids encoding the future auth flow:

* Principal identity is passed in by the API layer.
* Manager scopes are queried from the DB on demand.
* Authorisation decisions are explicit; helpers raise ``ScopeError`` with a
  reason so the API returns 403.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.enums import RoleName
from ..domain.errors import AuthError, ScopeError
from ..repositories.models import Store, User
from ..repositories.scopes import ManagerScopeRepository


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    tenant_id: str
    role: RoleName
    email: str
    # Optional host-provided ceiling used by a future trusted identity adapter.
    # ``None`` preserves the standalone role-derived capability set. A finite
    # set can only narrow authority; ``capabilities_for`` never lets it widen a
    # Grile role.
    capability_ceiling: frozenset[str] | None = None


def load_principal(session: Session, *, user_id: str, tenant_id: str) -> Principal:
    """Resolve a principal by user+tenant.

    Raises :class:`AuthError` if the principal is unknown, and
    :class:`ScopeError` if the role is missing.
    """

    stmt = select(User).where(User.id == user_id, User.tenant_id == tenant_id)
    user = session.execute(stmt).scalar_one_or_none()
    if user is None:
        raise AuthError("principal not found", details={"user_id": user_id, "tenant_id": tenant_id})
    if not user.is_active:
        raise AuthError("principal is inactive", details={"user_id": user_id})
    try:
        role = RoleName(user.role)
    except ValueError as exc:
        raise ScopeError(
            "principal has no recognised role",
            details={"user_id": user_id, "role": user.role},
        ) from exc
    return Principal(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=role,
        email=user.email,
    )


def assert_admin(principal: Principal) -> None:
    if principal.role is not RoleName.ADMIN:
        raise ScopeError(
            "admin role required",
            details={"principal": principal.user_id, "role": principal.role.value},
        )


def assert_manager_or_admin(principal: Principal) -> None:
    if principal.role not in {RoleName.ADMIN, RoleName.MANAGER}:
        raise ScopeError(
            "manager or admin role required",
            details={"principal": principal.user_id, "role": principal.role.value},
        )


def assert_same_tenant(principal: Principal, tenant_id: str) -> None:
    if principal.tenant_id != tenant_id:
        raise ScopeError(
            "principal cannot access other tenants",
            details={
                "principal_tenant": principal.tenant_id,
                "requested_tenant": tenant_id,
            },
        )


def effective_store_ids(session: Session, principal: Principal, on_date: date) -> set[str]:
    """Return the stores writable by a principal on an effective date.

    Admins are tenant-wide. Managers/TLs need an explicit effective-dated
    ``manager_scopes`` row; absence of a row is deny-by-default.
    """

    if principal.role is RoleName.ADMIN:
        stmt = select(Store.id).where(Store.tenant_id == principal.tenant_id)
        return set(session.execute(stmt).scalars())
    return ManagerScopeRepository(session).store_ids_for_user(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        on_date=on_date,
    )


def require_tenant(principal: Principal) -> str:
    """Return the tenant id the principal is allowed to use."""

    return principal.tenant_id


__all__ = [
    "Principal",
    "assert_admin",
    "assert_manager_or_admin",
    "assert_same_tenant",
    "effective_store_ids",
    "load_principal",
    "require_tenant",
]
