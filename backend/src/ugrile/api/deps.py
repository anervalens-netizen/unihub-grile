"""API dependencies.

The API is intentionally tiny at S1. The dependency provider returns a single
:class:`sqlalchemy.orm.Session` per request and resolves principals from the
``X-Ugrile-Identity`` and ``X-Ugrile-Tenant`` headers (skeleton auth; real
auth arrives with a later stage).
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from ..core.database import session_scope
from ..domain.errors import AuthError, ScopeError
from ..services.auth import Principal, load_principal


def db_session() -> Iterator[Session]:
    with session_scope() as session:
        yield session


def current_principal(
    session: Session = Depends(db_session),
    x_ugrile_identity: str | None = Header(default=None, alias="X-Ugrile-Identity"),
    x_ugrile_tenant: str | None = Header(default=None, alias="X-Ugrile-Tenant"),
) -> Principal:
    if not x_ugrile_identity or not x_ugrile_tenant:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing X-Ugrile-Identity and X-Ugrile-Tenant headers",
        )
    try:
        return load_principal(session, user_id=x_ugrile_identity, tenant_id=x_ugrile_tenant)
    except AuthError as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"code": exc.code, "message": exc.message}
        ) from exc
    except ScopeError as exc:
        raise HTTPException(
            status_code=exc.http_status, detail={"code": exc.code, "message": exc.message}
        ) from exc


__all__ = ["current_principal", "db_session"]
