"""Server-issued, single-use contracts for XLSX schedule imports."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.errors import ConflictError, StaleRevisionError, ValidationError
from ..repositories.models import Month, ScheduleImportContract

CONTRACT_TTL = timedelta(hours=24)


def _as_utc(value: datetime) -> datetime:
    """Normalise a stored datetime for UTC comparison.

    SQLite stores ``DateTime(timezone=True)`` values as naive strings, while
    PostgreSQL keeps the offset. Normalising both sides keeps expiry checks
    deterministic across dialects.
    """

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def catalog_hash(stores: dict[str, str], people: list[dict[str, str]]) -> str:
    return _digest(
        {
            "stores": sorted(stores.items()),
            "people": sorted(
                (
                    row["person_id"],
                    row.get("home_store_code", ""),
                    row.get("manager_code", ""),
                )
                for row in people
            ),
        }
    )


def scope_hash(allowed_by_date: dict[date, set[str]]) -> str:
    return _digest(
        [(day.isoformat(), sorted(store_ids)) for day, store_ids in sorted(allowed_by_date.items())]
    )


def issue_contract(
    session: Session,
    *,
    tenant_id: str,
    month: Month,
    user_id: str,
    base_revision: int,
    stores: dict[str, str],
    people: list[dict[str, str]],
    allowed_by_date: dict[date, set[str]],
) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    session.add(
        ScheduleImportContract(
            id=uuid4().hex,
            tenant_id=tenant_id,
            month_id=month.id,
            user_id=user_id,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            base_revision=base_revision,
            catalog_hash=catalog_hash(stores, people),
            scope_hash=scope_hash(allowed_by_date),
            issued_at=now,
            expires_at=now + CONTRACT_TTL,
        )
    )
    session.flush()
    return token


def validate_contract(
    session: Session,
    *,
    token: str,
    tenant_id: str,
    month: Month,
    user_id: str,
    parsed_tenant_id: str,
    parsed_month_id: str,
    parsed_revision: int,
    stores: dict[str, str],
    people: list[dict[str, str]],
    allowed_by_date: dict[date, set[str]],
    lock: bool = False,
) -> ScheduleImportContract:
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    statement = select(ScheduleImportContract).where(
        ScheduleImportContract.tenant_id == tenant_id,
        ScheduleImportContract.month_id == month.id,
        ScheduleImportContract.token_hash == token_hash,
    )
    if lock:
        statement = statement.with_for_update()
    contract = session.execute(statement).scalar_one_or_none()
    if contract is None or contract.user_id != user_id:
        raise ValidationError(
            "schedule Manifest contract is invalid or tampered",
            details={"code": "MANIFEST_TAMPERED"},
        )
    if contract.consumed_at is not None:
        raise ConflictError(
            "schedule import contract was already consumed",
            details={"code": "CONTRACT_CONSUMED", "contract_id": contract.id},
        )
    if _as_utc(contract.expires_at) <= datetime.now(UTC):
        raise ValidationError(
            "schedule import contract has expired",
            details={"code": "CONTRACT_EXPIRED", "contract_id": contract.id},
        )
    if parsed_tenant_id != tenant_id or parsed_month_id != month.id:
        raise ValidationError(
            "schedule Manifest identity does not match the contract",
            details={"code": "MANIFEST_TAMPERED"},
        )
    if parsed_revision != contract.base_revision:
        raise ValidationError(
            "schedule Manifest revision does not match the contract",
            details={
                "code": "MANIFEST_TAMPERED",
                "contract_revision": contract.base_revision,
                "provided": parsed_revision,
            },
        )
    if month.revision != contract.base_revision:
        raise StaleRevisionError(
            "stale schedule revision",
            details={
                "expected": contract.base_revision,
                "current": month.revision,
            },
        )
    if contract.catalog_hash != catalog_hash(stores, people) or contract.scope_hash != scope_hash(
        allowed_by_date
    ):
        raise ValidationError(
            "schedule catalog or effective scope changed since template issuance",
            details={"code": "CONTRACT_CHANGED", "contract_id": contract.id},
        )
    return contract


def consume_contract(session: Session, contract: ScheduleImportContract) -> None:
    contract.consumed_at = datetime.now(UTC)
    session.flush()


__all__ = [
    "CONTRACT_TTL",
    "catalog_hash",
    "consume_contract",
    "issue_contract",
    "scope_hash",
    "validate_contract",
]
