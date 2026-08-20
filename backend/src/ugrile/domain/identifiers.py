"""Identifier types.

Identifiers are explicit string newtypes so cross-table joins and connector
imports cannot mix tenants, stores, people, or months.
"""

from __future__ import annotations

from typing import Annotated, NewType

from pydantic import StringConstraints

# Identifier format: prefix + underscore + base36 token. The format is loose
# enough for fixtures yet strict enough to catch accidental name-as-id bugs.
IdentifierStr = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$"),
]


def make_tenant_id(token: str) -> str:
    return f"tenant_{_slug(token)}"


def make_store_id(token: str) -> str:
    return f"store_{_slug(token)}"


def make_person_id(token: str) -> str:
    return f"person_{_slug(token)}"


def make_month_id(tenant_token: str, year: int, month: int) -> str:
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range: {month}")
    return f"month_{_slug(tenant_token)}_{year:04d}-{month:02d}"


def make_user_id(token: str) -> str:
    return f"user_{_slug(token)}"


def make_role_id(token: str) -> str:
    return f"role_{_slug(token)}"


def make_scope_id(token: str) -> str:
    return f"scope_{_slug(token)}"


def make_assignment_id(tenant_token: str, store_token: str, business_date_iso: str) -> str:
    return f"asg_{_slug(tenant_token)}_{_slug(store_token)}_{business_date_iso}"


def make_absence_id(tenant_token: str, person_token: str, business_date_iso: str) -> str:
    return f"abs_{_slug(tenant_token)}_{_slug(person_token)}_{business_date_iso}"


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
