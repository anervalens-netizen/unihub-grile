"""Canonical Google Sheet projection format and reconciliation checksums.

The provider-specific code owns transport. This module owns only the stable
value-matrix shape and deterministic reconciliation fingerprints shared by the
fake and live providers.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from ..domain.errors import DomainError

FORMAT_VERSION = "v2"


def projection_metadata(
    payload: Mapping[str, Any],
    *,
    generation: str,
) -> dict[str, Any]:
    """Return normalized operator-visible metadata for one projection."""

    grila = _required_block(payload, "grila")
    raw = payload.get("metadata")
    metadata = raw if isinstance(raw, Mapping) else {}
    return {
        "format_version": FORMAT_VERSION,
        "generation": generation,
        "store_id": _cell(metadata.get("store_id")),
        "month_id": _cell(metadata.get("month_id")),
        "year": _cell(metadata.get("year")),
        "month": _cell(metadata.get("month")),
        "revision": _cell(metadata.get("revision", grila.get("revision"))),
        "rule_pack_version": _cell(metadata.get("rule_pack_version")),
        "projected_at": _cell(metadata.get("projected_at", grila.get("generated_at"))),
    }


def render_grila_values(
    payload: Mapping[str, Any],
    *,
    generation: str,
) -> list[list[Any]]:
    grila = _required_block(payload, "grila")
    metadata = projection_metadata(payload, generation=generation)
    target = grila.get("target")
    target_mapping = target if isinstance(target, Mapping) else {}
    values: list[list[Any]] = [
        ["UGRILE_PROJECTION", FORMAT_VERSION],
        ["generation", generation],
        ["store_id", metadata["store_id"]],
        ["month_id", metadata["month_id"]],
        ["year", metadata["year"]],
        ["month", metadata["month"]],
        ["revision", metadata["revision"]],
        ["rule_pack_version", metadata["rule_pack_version"]],
        ["projected_at", metadata["projected_at"]],
        ["target_amount", _cell(target_mapping.get("amount"))],
        ["target_currency", _cell(target_mapping.get("currency"))],
        ["target_version", _cell(target_mapping.get("version"))],
        ["target_sales_days", _cell(target_mapping.get("sales_days"))],
        ["", ""],
        ["business_date", "person_id", "status", "working_kind", "revision"],
    ]
    for row in _projection_rows(grila, "Grila"):
        values.append(
            [
                _cell(row.get("business_date")),
                _cell(row.get("person_id")),
                _cell(row.get("status")),
                _cell(row.get("working_kind")),
                _cell(row.get("revision")),
            ]
        )
    return values


def render_pontaj_values(
    payload: Mapping[str, Any],
    *,
    generation: str,
) -> list[list[Any]]:
    pontaj = _required_block(payload, "pontaj")
    metadata = projection_metadata(payload, generation=generation)
    values: list[list[Any]] = [
        ["UGRILE_PROJECTION", FORMAT_VERSION],
        ["generation", generation],
        ["store_id", metadata["store_id"]],
        ["month_id", metadata["month_id"]],
        ["year", metadata["year"]],
        ["month", metadata["month"]],
        ["revision", metadata["revision"]],
        ["rule_pack_version", metadata["rule_pack_version"]],
        ["projected_at", metadata["projected_at"]],
        ["", ""],
        [
            "person_id",
            "business_date",
            "status",
            "start_time",
            "end_time",
            "pause_minutes",
            "hours",
        ],
    ]
    for row in _projection_rows(pontaj, "Pontaj"):
        values.append(
            [
                _cell(row.get("person_id")),
                _cell(row.get("business_date")),
                _cell(row.get("status")),
                _cell(row.get("start_time")),
                _cell(row.get("end_time")),
                _cell(row.get("pause_minutes")),
                _cell(row.get("hours")),
            ]
        )
    return values


def normalize_matrix(
    rows: Sequence[Sequence[Any]],
    *,
    width: int,
) -> list[list[Any]]:
    """Normalize Sheets' omitted trailing cells into a fixed-width matrix."""

    result: list[list[Any]] = []
    for row in rows:
        normalized = [_cell(value) for value in list(row[:width])]
        normalized.extend([""] * (width - len(normalized)))
        result.append(normalized)
    return result


def matrix_checksum(rows: Sequence[Sequence[Any]], *, width: int) -> str:
    encoded = json.dumps(
        normalize_matrix(rows, width=width),
        sort_keys=False,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def reconciliation_metadata(
    payload: Mapping[str, Any],
    *,
    generation: str,
    verification_mode: str,
    verified: bool,
    grila_values: Sequence[Sequence[Any]] | None = None,
    pontaj_values: Sequence[Sequence[Any]] | None = None,
) -> dict[str, Any]:
    """Build the persisted/API reconciliation envelope for one accepted projection."""

    grila = _required_block(payload, "grila")
    pontaj = _required_block(payload, "pontaj")
    rendered_grila = (
        list(grila_values)
        if grila_values is not None
        else render_grila_values(payload, generation=generation)
    )
    rendered_pontaj = (
        list(pontaj_values)
        if pontaj_values is not None
        else render_pontaj_values(payload, generation=generation)
    )
    grila_checksum = matrix_checksum(rendered_grila, width=5)
    pontaj_checksum = matrix_checksum(rendered_pontaj, width=7)
    projection_checksum = hashlib.sha256(
        json.dumps(
            {"grila": grila_checksum, "pontaj": pontaj_checksum},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    metadata = projection_metadata(payload, generation=generation)
    return {
        **metadata,
        "verification_mode": verification_mode,
        "verified": verified,
        "grila_rows": len(_projection_rows(grila, "Grila")),
        "pontaj_rows": len(_projection_rows(pontaj, "Pontaj")),
        "grila_checksum_sha256": grila_checksum,
        "pontaj_checksum_sha256": pontaj_checksum,
        "projection_checksum_sha256": projection_checksum,
    }


def matrices_match(
    expected: Sequence[Sequence[Any]],
    actual: Sequence[Sequence[Any]],
    *,
    width: int,
) -> bool:
    """Compare a bounded write/readback while tolerating omitted blank tail rows.

    Sheets Values GET may omit trailing rows that are entirely blank. Those
    omissions are semantically equivalent to the explicit blank padding we
    write to clear stale rows. Any returned non-blank stale row still causes a
    mismatch because the read range is bounded to the exact padded write size.
    """

    expected_matrix = normalize_matrix(expected, width=width)
    actual_matrix = normalize_matrix(actual, width=width)
    if len(actual_matrix) > len(expected_matrix):
        return False
    blank_row = [""] * width
    actual_matrix.extend([list(blank_row) for _ in range(len(expected_matrix) - len(actual_matrix))])
    return expected_matrix == actual_matrix


def _required_block(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    block = payload.get(key)
    if not isinstance(block, Mapping):
        raise DomainError(
            f"{key} projection block must be a mapping",
            details={"code": "GOOGLE_PROJECTION_STRUCTURE_INVALID", "sheet": key},
        )
    return block


def _projection_rows(block: Mapping[str, Any], label: str) -> list[Mapping[str, Any]]:
    rows = block.get("rows", [])
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise DomainError(
            f"{label} projection rows must be a list of mappings",
            details={"code": "GOOGLE_PROJECTION_ROWS_INVALID", "sheet": label},
        )
    return list(rows)


def _cell(value: Any) -> str | int | float | bool:
    if value is None:
        return ""
    if isinstance(value, bool | int | float | str):
        return value
    return str(value)


__all__ = [
    "FORMAT_VERSION",
    "matrices_match",
    "matrix_checksum",
    "normalize_matrix",
    "projection_metadata",
    "reconciliation_metadata",
    "render_grila_values",
    "render_pontaj_values",
]
