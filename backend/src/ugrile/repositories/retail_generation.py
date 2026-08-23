"""Accepted Retail snapshot generation ledger over append-only ImportRun rows.

A successful Retail snapshot apply and its DONE ImportRun are committed in the
same caller transaction. The newest DONE run for a tenant/period is therefore
the accepted head; failed/rolled-back attempts cannot displace last-good state.

The ledger also pins the exact target and incentive row versions materialized by
that accepted snapshot. This prevents a later complete snapshot that omits an
input from silently falling back to an older database version.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.errors import ConnectorError
from .models import ImportRun

RETAIL_IMPORT_KIND = "RETAIL_SNAPSHOT_V1"


@dataclass(frozen=True, slots=True)
class AcceptedRetailGeneration:
    import_run_id: int
    tenant_id: str
    period: str
    generation_key: str
    snapshot_sha256: str
    schema_version: str
    sales_hash: str
    sales_revision: int
    campaign_revision: int
    cutoff_date: str
    target_versions: tuple[tuple[str, str, int], ...]
    incentive_versions: tuple[tuple[str, int], ...]

    def target_version_for(self, *, store_id: str, kind: str) -> int | None:
        for row_store_id, row_kind, version in self.target_versions:
            if row_store_id == store_id and row_kind == kind:
                return version
        return None

    def incentive_version_for(self, *, person_id: str) -> int | None:
        for row_person_id, version in self.incentive_versions:
            if row_person_id == person_id:
                return version
        return None


def _invalid_ledger(row: ImportRun, key: str) -> ConnectorError:
    return ConnectorError(
        "accepted Retail generation ledger entry is incomplete",
        details={
            "code": "RETAIL_GENERATION_LEDGER_INVALID",
            "import_run_id": row.id,
            "missing_or_invalid": key,
        },
    )


def _decode_target_versions(
    row: ImportRun,
    payload: dict[str, object],
) -> tuple[tuple[str, str, int], ...]:
    raw = payload.get("target_versions")
    if not isinstance(raw, list):
        raise _invalid_ledger(row, "target_versions")
    decoded: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise _invalid_ledger(row, f"target_versions[{index}]")
        store_id = item.get("store_id")
        kind = item.get("kind")
        version = item.get("version")
        if (
            not isinstance(store_id, str)
            or not store_id
            or not isinstance(kind, str)
            or not kind
            or type(version) is not int
            or version < 1
        ):
            raise _invalid_ledger(row, f"target_versions[{index}]")
        key = (store_id, kind)
        if key in seen:
            raise _invalid_ledger(row, "target_versions.duplicate")
        seen.add(key)
        decoded.append((store_id, kind, version))
    return tuple(sorted(decoded))


def _decode_incentive_versions(
    row: ImportRun,
    payload: dict[str, object],
) -> tuple[tuple[str, int], ...]:
    raw = payload.get("incentive_versions")
    if not isinstance(raw, list):
        raise _invalid_ledger(row, "incentive_versions")
    decoded: list[tuple[str, int]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise _invalid_ledger(row, f"incentive_versions[{index}]")
        person_id = item.get("person_id")
        version = item.get("version")
        if (
            not isinstance(person_id, str)
            or not person_id
            or type(version) is not int
            or version < 1
        ):
            raise _invalid_ledger(row, f"incentive_versions[{index}]")
        if person_id in seen:
            raise _invalid_ledger(row, "incentive_versions.duplicate")
        seen.add(person_id)
        decoded.append((person_id, version))
    return tuple(sorted(decoded))


def _decode_run(row: ImportRun) -> AcceptedRetailGeneration:
    try:
        payload = json.loads(row.summary or "{}")
    except json.JSONDecodeError as exc:
        raise ConnectorError(
            "accepted Retail generation ledger contains invalid JSON",
            details={
                "code": "RETAIL_GENERATION_LEDGER_INVALID",
                "import_run_id": row.id,
            },
        ) from exc
    if not isinstance(payload, dict):
        raise ConnectorError(
            "accepted Retail generation ledger entry is not an object",
            details={
                "code": "RETAIL_GENERATION_LEDGER_INVALID",
                "import_run_id": row.id,
            },
        )
    required = {
        "period": str,
        "generation_key": str,
        "snapshot_sha256": str,
        "schema_version": str,
        "sales_hash": str,
        "sales_revision": int,
        "campaign_revision": int,
        "cutoff_date": str,
    }
    for key, expected_type in required.items():
        value = payload.get(key)
        if expected_type is int:
            valid = type(value) is int
        else:
            valid = isinstance(value, expected_type)
        if not valid:
            raise _invalid_ledger(row, key)
    return AcceptedRetailGeneration(
        import_run_id=row.id,
        tenant_id=row.tenant_id,
        period=str(payload["period"]),
        generation_key=str(payload["generation_key"]),
        snapshot_sha256=str(payload["snapshot_sha256"]),
        schema_version=str(payload["schema_version"]),
        sales_hash=str(payload["sales_hash"]),
        sales_revision=int(payload["sales_revision"]),
        campaign_revision=int(payload["campaign_revision"]),
        cutoff_date=str(payload["cutoff_date"]),
        target_versions=_decode_target_versions(row, payload),
        incentive_versions=_decode_incentive_versions(row, payload),
    )


def accepted_retail_generation(
    session: Session,
    *,
    tenant_id: str,
    period: str,
) -> AcceptedRetailGeneration | None:
    """Return the last committed accepted Retail generation for the period."""

    rows = list(
        session.execute(
            select(ImportRun)
            .where(
                ImportRun.tenant_id == tenant_id,
                ImportRun.kind == RETAIL_IMPORT_KIND,
                ImportRun.status == "DONE",
            )
            .order_by(ImportRun.id.desc())
        ).scalars()
    )
    for row in rows:
        decoded = _decode_run(row)
        if decoded.period == period:
            return decoded
    return None


def accepted_retail_generation_key(
    session: Session,
    *,
    tenant_id: str,
    period: str,
) -> str | None:
    accepted = accepted_retail_generation(
        session,
        tenant_id=tenant_id,
        period=period,
    )
    return accepted.generation_key if accepted is not None else None


__all__ = [
    "AcceptedRetailGeneration",
    "RETAIL_IMPORT_KIND",
    "accepted_retail_generation",
    "accepted_retail_generation_key",
]
