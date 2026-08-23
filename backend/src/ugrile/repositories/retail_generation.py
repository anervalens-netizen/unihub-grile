"""Accepted Retail snapshot generation ledger over append-only ImportRun rows.

A successful Retail snapshot apply and its DONE ImportRun are committed in the
same caller transaction. The newest DONE run for a tenant/period is therefore
the accepted head; failed/rolled-back attempts cannot displace last-good state.

The ledger pins exact catalog membership plus target/incentive row versions for
the accepted snapshot. A newer complete snapshot can therefore never fall back
to old financial rows.

Hot read paths may embed :func:`accepted_retail_generation_summary_subquery`
inside their existing SELECT. This preserves accepted-head filtering without an
extra SQL round-trip and keeps the calibrated M3 query-count budgets unchanged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.sql.selectable import ScalarSelect

from ..domain.errors import ConnectorError
from .models import ImportRun

RETAIL_IMPORT_KIND = "RETAIL_SNAPSHOT_V1"
_PERIOD_RE = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")


def retail_import_kind(period: str) -> str:
    if not _PERIOD_RE.fullmatch(period):
        raise ConnectorError(
            "Retail generation period is invalid",
            details={"code": "RETAIL_PERIOD_INVALID", "period": period},
        )
    return f"{RETAIL_IMPORT_KIND}:{period}"


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
    store_ids: tuple[str, ...]
    person_ids: tuple[str, ...]
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


def _decode_unique_strings(
    row: ImportRun,
    payload: dict[str, object],
    key: str,
) -> tuple[str, ...]:
    raw = payload.get(key)
    if not isinstance(raw, list):
        raise _invalid_ledger(row, key)
    decoded: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item:
            raise _invalid_ledger(row, f"{key}[{index}]")
        decoded.append(item)
    if len(decoded) != len(set(decoded)):
        raise _invalid_ledger(row, f"{key}.duplicate")
    return tuple(sorted(decoded))


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
        identity = (store_id, kind)
        if identity in seen:
            raise _invalid_ledger(row, "target_versions.duplicate")
        seen.add(identity)
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
        valid = type(value) is int if expected_type is int else isinstance(value, expected_type)
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
        store_ids=_decode_unique_strings(row, payload, "store_ids"),
        person_ids=_decode_unique_strings(row, payload, "person_ids"),
        target_versions=_decode_target_versions(row, payload),
        incentive_versions=_decode_incentive_versions(row, payload),
    )


def accepted_retail_generation_summary_subquery(
    *,
    tenant_id: str,
    period: str,
) -> ScalarSelect[str]:
    """Return the accepted-head summary as an embeddable scalar subquery.

    A missing row evaluates to SQL ``NULL``. Callers can therefore preserve
    legacy all-generation semantics with ``OR summary IS NULL`` while pinning a
    Retail-backed period to the generation marker inside the canonical summary.
    This is deliberately a scalar subquery rather than a separate repository
    read so hot paths do not gain another SELECT round-trip.
    """

    return (
        select(ImportRun.summary)
        .where(
            ImportRun.tenant_id == tenant_id,
            ImportRun.kind == retail_import_kind(period),
            ImportRun.status == "DONE",
        )
        .order_by(ImportRun.id.desc())
        .limit(1)
        .scalar_subquery()
    )


def accepted_retail_generation(
    session: Session,
    *,
    tenant_id: str,
    period: str,
) -> AcceptedRetailGeneration | None:
    """Return the last committed accepted Retail generation for the period."""

    row = session.execute(
        select(ImportRun)
        .where(
            ImportRun.tenant_id == tenant_id,
            ImportRun.kind == retail_import_kind(period),
            ImportRun.status == "DONE",
        )
        .order_by(ImportRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    decoded = _decode_run(row)
    if decoded.period != period:
        raise ConnectorError(
            "accepted Retail generation ledger period does not match its key",
            details={
                "code": "RETAIL_GENERATION_LEDGER_INVALID",
                "import_run_id": row.id,
                "expected_period": period,
                "received_period": decoded.period,
            },
        )
    return decoded


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
    "accepted_retail_generation_summary_subquery",
    "retail_import_kind",
]
