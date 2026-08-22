"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.database import session_scope
from ..domain.errors import AuthError, ScopeError
from ..services.auth import Principal
from ..services.principal_provider import DEV_HEADER_PROVIDER


def db_session() -> Iterator[Session]:
    with session_scope() as session:
        yield session


def current_principal(
    session: Session = Depends(db_session),
    x_ugrile_identity: str | None = Header(default=None, alias="X-Ugrile-Identity"),
    x_ugrile_tenant: str | None = Header(default=None, alias="X-Ugrile-Tenant"),
) -> Principal:
    """Resolve the request principal through the configured provider.

    Development headers are explicit and are never accepted in ``prod``. The
    future Retail/external provider is a reserved contract; until an adapter is
    installed the standalone application fails closed instead of trusting a
    caller-supplied identity.
    """

    settings = get_settings()
    if settings.identity_provider == "dev_headers":
        if settings.app_env == "prod":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "IDENTITY_PROVIDER_UNSAFE",
                    "message": "development identity headers are disabled in prod",
                },
            )
        provider = DEV_HEADER_PROVIDER
    else:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "IDENTITY_PROVIDER_NOT_CONFIGURED",
                "message": "external identity provider adapter is not configured",
            },
        )

    try:
        return provider.resolve(
            session,
            identity=x_ugrile_identity,
            tenant=x_ugrile_tenant,
        )
    except AuthError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc
    except ScopeError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": exc.code, "message": exc.message, "details": exc.details},
        ) from exc


__all__ = ["current_principal", "db_session"]
