"""Identifier types.

Identifiers are explicit string newtypes so cross-table joins and connector
imports cannot mix tenants, stores, people, or months.

Every business identifier (store, person) is **tenant-scoped**: the slug embeds
the tenant token so two tenants can never collide on the same primary key.
The application code and the DB foreign keys both rely on this invariant.

Format:

* ``store_<tenant_slug>_<store_slug>``
* ``person_<tenant_slug>_<person_slug>``
* ``month_<tenant_slug>_<YYYY>-<MM>``
* ``tenant_<tenant_slug>``
* ``user_<tenant_slug>_<user_slug>`` etc.

The slug is restricted to lowercase alphanumeric + underscore. The composite
shape means the foreign keys are always composite — a row that points to a
``store_id`` must also carry a matching ``tenant_id``. The schema enforces
this with composite foreign keys; the application asserts it on every read.

The legacy single-argument helpers are kept as aliases for code that only
needs to derive an opaque token (for example, idempotency keys); they never
produce a foreign-key target.
"""

from __future__ import annotations

from typing import Annotated, NewType

from pydantic import StringConstraints

# Identifier format: lowercase alphanumeric + underscore. Strict enough to
# catch accidental name-as-id bugs; loose enough for fixtures.
IdentifierStr = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[a-z0-9_]+$"),
]


def make_tenant_id(token: str) -> str:
    return f"tenant_{_slug(token)}"


def make_store_id(tenant_token: str, store_token: str) -> str:
    """Tenant-scoped store id.

    The tenant slug is part of the id so two tenants with the same internal
    store code never share a primary key. The DB composite FK on
    ``(tenant_id, store_id)`` reinforces the invariant.
    """

    return f"store_{_slug(tenant_token)}_{_slug(store_token)}"


def make_person_id(tenant_token: str, person_token: str) -> str:
    """Tenant-scoped person id."""

    return f"person_{_slug(tenant_token)}_{_slug(person_token)}"


def make_month_id(tenant_token: str, year: int, month: int) -> str:
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range: {month}")
    return f"month_{_slug(tenant_token)}_{year:04d}-{month:02d}"


def make_user_id(tenant_token: str, user_token: str) -> str:
    return f"user_{_slug(tenant_token)}_{_slug(user_token)}"


def make_role_id(token: str) -> str:
    return f"role_{_slug(token)}"


def make_scope_id(token: str) -> str:
    return f"scope_{_slug(token)}"


def make_assignment_id(tenant_token: str, store_token: str, business_date_iso: str) -> str:
    return f"asg_{_slug(tenant_token)}_{_slug(store_token)}_{business_date_iso}"


def make_absence_id(tenant_token: str, person_token: str, business_date_iso: str) -> str:
    return f"abs_{_slug(tenant_token)}_{_slug(person_token)}_{business_date_iso}"


def make_target_id(tenant_token: str, store_token: str, year: int, month: int, kind: str) -> str:
    return (
        f"target_{_slug(tenant_token)}_{_slug(store_token)}"
        f"_{year:04d}-{month:02d}_{_slug(kind)}"
    )


def tenant_slug_from_tenant_id(tenant_id: str) -> str:
    """Return the tenant slug embedded in a tenant id.

    The inverse of ``make_tenant_id``. Raises ``ValueError`` when the input is
    not a ``tenant_`` prefixed id — callers must not silently fall back.
    """

    if not tenant_id.startswith("tenant_"):
        raise ValueError(f"not a tenant id: {tenant_id!r}")
    return tenant_id[len("tenant_") :]


def store_slug_from_store_id(store_id: str) -> tuple[str, str]:
    """Return ``(tenant_slug, store_slug)`` for a tenant-scoped store id."""

    if not store_id.startswith("store_"):
        raise ValueError(f"not a store id: {store_id!r}")
    body = store_id[len("store_") :]
    parts = body.split("_", 1)
    if len(parts) != 2:
        raise ValueError(f"malformed store id: {store_id!r}")
    return parts[0], parts[1]


def person_slug_from_person_id(person_id: str) -> tuple[str, str]:
    """Return ``(tenant_slug, person_slug)`` for a tenant-scoped person id."""

    if not person_id.startswith("person_"):
        raise ValueError(f"not a person id: {person_id!r}")
    body = person_id[len("person_") :]
    parts = body.split("_", 1)
    if len(parts) != 2:
        raise ValueError(f"malformed person id: {person_id!r}")
    return parts[0], parts[1]


def _slug(token: str) -> str:
    out: list[str] = []
    for ch in token.strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in {"-", " ", ".", "/", ":"}:
            out.append("_")
    value = "".join(out).strip("_")
    if not value:
        raise ValueError(f"identifier token is empty after normalisation: {token!r}")
    return value


# Convenience aliases for type hints.
TenantId = NewType("TenantId", str)
StoreId = NewType("StoreId", str)
PersonId = NewType("PersonId", str)
MonthId = NewType("MonthId", str)
UserId = NewType("UserId", str)
RoleId = NewType("RoleId", str)
ScopeId = NewType("ScopeId", str)


__all__ = [
    "IdentifierStr",
    "PersonId",
    "RoleId",
    "ScopeId",
    "StoreId",
    "TenantId",
    "UserId",
    "MonthId",
    "make_absence_id",
    "make_assignment_id",
    "make_month_id",
    "make_person_id",
    "make_role_id",
    "make_scope_id",
    "make_store_id",
    "make_target_id",
    "make_tenant_id",
    "make_user_id",
    "person_slug_from_person_id",
    "store_slug_from_store_id",
    "tenant_slug_from_tenant_id",
]
