"""Explicit lifecycle for permanent store-to-Google-Sheet bindings.

Bindings are store-level operational configuration, not projection output. A
projection may advance a binding generation after a successful publication, but
it must never create or re-point a live binding as a side effect.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.errors import ConflictError, DomainError, NotFoundError
from ..repositories.models import SheetBinding, Store
from .audit import record_audit_event

UNPROJECTED_GENERATION = "UNPROJECTED"


@dataclass(frozen=True, slots=True)
class SheetBindingChange:
    binding: SheetBinding
    created: bool
    changed: bool


def get_sheet_binding(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    lock: bool = False,
) -> SheetBinding | None:
    stmt = select(SheetBinding).where(
        SheetBinding.tenant_id == tenant_id,
        SheetBinding.store_id == store_id,
    )
    if lock:
        stmt = stmt.with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def require_sheet_binding(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
) -> SheetBinding:
    binding = get_sheet_binding(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
    )
    if binding is None:
        raise NotFoundError(
            "sheet binding not found",
            details={"code": "SHEET_BINDING_NOT_FOUND", "store_id": store_id},
        )
    return binding


def configure_sheet_binding(
    session: Session,
    *,
    tenant_id: str,
    store_id: str,
    spreadsheet_id: str,
    sheet_name_grila: str,
    sheet_name_pontaj: str,
    actor_id: str,
    expected_current_spreadsheet_id: str | None = None,
    reason: str | None = None,
) -> SheetBindingChange:
    """Create or explicitly rebind one store under a row lock.

    Initial creation is idempotent. Changing an existing identity requires a
    compare-and-swap value (``expected_current_spreadsheet_id``) plus a reason.
    An exact replay of an already-applied identity is also idempotent, even if
    the caller still carries the previous CAS value after losing the response.
    A Google spreadsheet may belong to only one store globally.
    """

    spreadsheet_id = spreadsheet_id.strip()
    sheet_name_grila = sheet_name_grila.strip()
    sheet_name_pontaj = sheet_name_pontaj.strip()
    if not spreadsheet_id or not sheet_name_grila or not sheet_name_pontaj:
        raise DomainError(
            "spreadsheet and tab identifiers must be non-empty",
            details={"code": "SHEET_BINDING_INVALID"},
        )
    if sheet_name_grila == sheet_name_pontaj:
        raise DomainError(
            "Grila and Pontaj must use different tabs",
            details={"code": "SHEET_BINDING_TABS_COLLIDE"},
        )

    store_exists = session.execute(
        select(Store.id).where(Store.tenant_id == tenant_id, Store.id == store_id)
    ).scalar_one_or_none()
    if store_exists is None:
        raise NotFoundError(
            "store not found",
            details={"code": "STORE_NOT_FOUND", "store_id": store_id},
        )

    binding = get_sheet_binding(
        session,
        tenant_id=tenant_id,
        store_id=store_id,
        lock=True,
    )

    conflicting_sheet = session.execute(
        select(SheetBinding.id).where(
            SheetBinding.spreadsheet_id == spreadsheet_id,
            ~(
                (SheetBinding.tenant_id == tenant_id)
                & (SheetBinding.store_id == store_id)
            ),
        )
    ).scalar_one_or_none()
    if conflicting_sheet is not None:
        raise ConflictError(
            "Google spreadsheet is already bound to another store",
            details={"code": "SHEET_SPREADSHEET_ALREADY_BOUND"},
        )

    if binding is None:
        if expected_current_spreadsheet_id is not None:
            raise ConflictError(
                "sheet binding does not exist at the expected identity",
                details={"code": "SHEET_BINDING_STALE"},
            )
        binding = SheetBinding(
            tenant_id=tenant_id,
            store_id=store_id,
            spreadsheet_id=spreadsheet_id,
            sheet_name_grila=sheet_name_grila,
            sheet_name_pontaj=sheet_name_pontaj,
            generation=UNPROJECTED_GENERATION,
        )
        session.add(binding)
        session.flush()
        record_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action="SHEET_BIND_CREATE",
            entity="sheet_binding",
            entity_id=store_id,
            payload={
                "before": None,
                "after": {
                    "spreadsheet_id": spreadsheet_id,
                    "sheet_name_grila": sheet_name_grila,
                    "sheet_name_pontaj": sheet_name_pontaj,
                },
            },
        )
        return SheetBindingChange(binding=binding, created=True, changed=True)

    current_identity = (
        binding.spreadsheet_id,
        binding.sheet_name_grila,
        binding.sheet_name_pontaj,
    )
    requested_identity = (spreadsheet_id, sheet_name_grila, sheet_name_pontaj)
    if current_identity == requested_identity:
        return SheetBindingChange(binding=binding, created=False, changed=False)

    if expected_current_spreadsheet_id != binding.spreadsheet_id:
        raise ConflictError(
            "sheet binding changed since it was read",
            details={"code": "SHEET_BINDING_STALE"},
        )
    clean_reason = (reason or "").strip()
    if len(clean_reason) < 4:
        raise DomainError(
            "rebind reason is required",
            details={"code": "SHEET_REBIND_REASON_REQUIRED"},
        )

    before = {
        "spreadsheet_id": binding.spreadsheet_id,
        "sheet_name_grila": binding.sheet_name_grila,
        "sheet_name_pontaj": binding.sheet_name_pontaj,
        "generation": binding.generation,
    }
    binding.spreadsheet_id = spreadsheet_id
    binding.sheet_name_grila = sheet_name_grila
    binding.sheet_name_pontaj = sheet_name_pontaj
    binding.generation = UNPROJECTED_GENERATION
    session.flush()
    record_audit_event(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action="SHEET_BIND_REPLACE",
        entity="sheet_binding",
        entity_id=store_id,
        payload={
            "reason": clean_reason,
            "before": before,
            "after": {
                "spreadsheet_id": spreadsheet_id,
                "sheet_name_grila": sheet_name_grila,
                "sheet_name_pontaj": sheet_name_pontaj,
                "generation": UNPROJECTED_GENERATION,
            },
        },
    )
    return SheetBindingChange(binding=binding, created=False, changed=True)


__all__ = [
    "SheetBindingChange",
    "UNPROJECTED_GENERATION",
    "configure_sheet_binding",
    "get_sheet_binding",
    "require_sheet_binding",
]
