"""Canonical HTTP error-envelope helpers.

Every API failure that reaches FastAPI is normalized to the same top-level
shape::

    {"code": str, "message": str, "details": object}

Domain errors already carry typed codes. Some older services attach a more
specific semantic code (for example ``MONTH_CLOSED``) inside ``details`` while
the class itself is a generic ``CONFLICT``/``MONTH_STATE``; those semantic codes
are promoted without changing the HTTP status. FastAPI/Starlette HTTP errors
and request-validation failures are normalized here as well so clients never
need to understand the framework's legacy ``{"detail": ...}`` wrapper.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..domain.errors import DomainError

_GENERIC_PROMOTABLE_CODES = {"CONFLICT", "MONTH_STATE"}


def _details_dict(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def _typed_code(code: str, details: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return the most specific stable code and remove duplicate code metadata."""

    semantic = details.get("code")
    if isinstance(semantic, str) and (code in _GENERIC_PROMOTABLE_CODES or semantic == code):
        cleaned = dict(details)
        cleaned.pop("code", None)
        return semantic, cleaned
    return code, details


def error_content(
    *,
    code: str,
    message: str,
    details: object = None,
) -> dict[str, Any]:
    normalized_details = _details_dict(details)
    typed_code, normalized_details = _typed_code(code, normalized_details)
    return {
        "code": typed_code,
        "message": message,
        "details": normalized_details,
    }


def domain_error_response(exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http_status,
        content=jsonable_encoder(
            error_content(code=exc.code, message=exc.message, details=exc.details)
        ),
    )


def http_error_response(exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, Mapping):
        mapped = _details_dict(detail)
        raw_code = mapped.get("code")
        raw_message = mapped.get("message")
        code = str(raw_code) if isinstance(raw_code, str) else f"HTTP_{exc.status_code}"
        message = (
            str(raw_message)
            if isinstance(raw_message, str)
            else f"HTTP {exc.status_code} error"
        )
        details = mapped.get("details")
    else:
        code = f"HTTP_{exc.status_code}"
        message = str(detail) if detail else f"HTTP {exc.status_code} error"
        details = None

    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(error_content(code=code, message=message, details=details)),
        headers=exc.headers,
    )


def validation_error_response(exc: RequestValidationError) -> JSONResponse:
    # Do not echo ``exc.body``: request bodies can contain data that should not
    # be reflected into logs/UI. Pydantic's structured locations/messages are
    # sufficient for deterministic client handling.
    return JSONResponse(
        status_code=422,
        content={
            "code": "REQUEST_VALIDATION_ERROR",
            "message": "request validation failed",
            "details": {"errors": jsonable_encoder(exc.errors())},
        },
    )


__all__ = [
    "domain_error_response",
    "error_content",
    "http_error_response",
    "validation_error_response",
]
