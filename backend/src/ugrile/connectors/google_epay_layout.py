"""Canonical Google Sheet E-pay input layout and protection contract.

The managed Grila projection remains in columns A:E. E-pay is deliberately
isolated in G:I so operator-editable values never participate in the projection
checksum and can survive projection refreshes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

EPAY_FORMAT_VERSION = "v1"
EPAY_MARKER = "UGRILE_EPAY_INPUTS"
EPAY_MAX_DATA_ROWS = 256
EPAY_READ_END_ROW = EPAY_MAX_DATA_ROWS + 4
EPAY_PROTECTION_PREFIX = "UGRILE_MANAGED_PROTECTION:v1:"


def _row(values: list[Any], width: int = 3) -> list[Any]:
    result = list(values[:width])
    result.extend([""] * (width - len(result)))
    return result


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _nonempty(row: list[Any]) -> bool:
    return any(_text(value) for value in row)


@dataclass(frozen=True, slots=True)
class EpayLayoutReadback:
    observations: tuple[dict[str, Any], ...]
    structure_valid: bool
    structural_errors: tuple[str, ...]


def epay_read_range(sheet_name: str) -> str:
    return f"{_quote_sheet(sheet_name)}!G1:I{EPAY_READ_END_ROW}"


def epay_write_range(sheet_name: str, person_count: int) -> str:
    return f"{_quote_sheet(sheet_name)}!G1:I{max(4 + person_count, 4)}"


def render_epay_values(
    *,
    month_id: str,
    revision: int,
    person_ids: list[str],
    preserved: dict[str, tuple[Any, Any]] | None = None,
) -> list[list[Any]]:
    people = sorted(set(person_ids))
    if len(people) > EPAY_MAX_DATA_ROWS:
        raise ValueError("E-pay person set exceeds bounded Sheet layout")
    existing = preserved or {}
    rows: list[list[Any]] = [
        [EPAY_MARKER, EPAY_FORMAT_VERSION, ""],
        ["month_id", month_id, ""],
        ["revision", revision, ""],
        ["person_id", "UNDER_50", "AT_OR_OVER_50"],
    ]
    for person_id in people:
        under_50, at_or_over_50 = existing.get(person_id, ("", ""))
        rows.append([person_id, under_50, at_or_over_50])
    return rows


def preserved_epay_values(
    rows: list[list[Any]],
    *,
    month_id: str,
) -> dict[str, tuple[Any, Any]]:
    """Return prior raw inputs only when the remote block belongs to this month."""

    normalized = [_row(row) for row in rows]
    if len(normalized) < 4:
        return {}
    if normalized[0][0] != EPAY_MARKER or normalized[0][1] != EPAY_FORMAT_VERSION:
        return {}
    if normalized[1][0] != "month_id" or _text(normalized[1][1]) != month_id:
        return {}
    if normalized[3] != ["person_id", "UNDER_50", "AT_OR_OVER_50"]:
        return {}
    result: dict[str, tuple[Any, Any]] = {}
    for row in normalized[4:]:
        if not _nonempty(row):
            continue
        person_id = _text(row[0])
        if not person_id or person_id in result:
            return {}
        result[person_id] = (row[1], row[2])
    return result


def parse_epay_readback(
    rows: list[list[Any]],
    *,
    month_id: str,
    revision: int,
    expected_person_ids: list[str],
) -> EpayLayoutReadback:
    """Parse a bounded G:I readback and fail closed on any structural drift."""

    expected = sorted(set(expected_person_ids))
    if len(expected) > EPAY_MAX_DATA_ROWS:
        raise ValueError("E-pay person set exceeds bounded Sheet layout")
    normalized = [_row(row) for row in rows]
    errors: list[str] = []
    if len(normalized) < 4:
        errors.append("EPAY_LAYOUT_TRUNCATED")
        normalized.extend([["", "", ""] for _ in range(4 - len(normalized))])
    if normalized[0] != [EPAY_MARKER, EPAY_FORMAT_VERSION, ""]:
        errors.append("EPAY_LAYOUT_MARKER_MISMATCH")
    if normalized[1][0] != "month_id" or _text(normalized[1][1]) != month_id:
        errors.append("EPAY_LAYOUT_MONTH_MISMATCH")
    if normalized[2][0] != "revision" or _text(normalized[2][1]) != str(revision):
        errors.append("EPAY_LAYOUT_REVISION_MISMATCH")
    if normalized[3] != ["person_id", "UNDER_50", "AT_OR_OVER_50"]:
        errors.append("EPAY_LAYOUT_HEADER_MISMATCH")

    values_by_person: dict[str, tuple[Any, Any]] = {}
    duplicate = False
    for row in normalized[4:]:
        if not _nonempty(row):
            continue
        person_id = _text(row[0])
        if not person_id or person_id in values_by_person:
            duplicate = True
            continue
        values_by_person[person_id] = (row[1], row[2])
    if duplicate:
        errors.append("EPAY_LAYOUT_DUPLICATE_PERSON")
    if sorted(values_by_person) != expected:
        errors.append("EPAY_LAYOUT_PERSON_SET_MISMATCH")

    structure_valid = not errors
    observations: list[dict[str, Any]] = []
    for person_id in expected:
        under_50, at_or_over_50 = values_by_person.get(person_id, (None, None))
        if not structure_valid:
            under_50 = None
            at_or_over_50 = None
        observations.extend(
            [
                {"person_id": person_id, "category": "UNDER_50", "value": under_50},
                {
                    "person_id": person_id,
                    "category": "AT_OR_OVER_50",
                    "value": at_or_over_50,
                },
            ]
        )
    return EpayLayoutReadback(
        observations=tuple(observations),
        structure_valid=structure_valid,
        structural_errors=tuple(dict.fromkeys(errors)),
    )


def managed_protection_description(tab_name: str) -> str:
    return f"{EPAY_PROTECTION_PREFIX}{tab_name}"


def grila_unprotected_range(*, sheet_id: int, person_count: int) -> dict[str, int] | None:
    if person_count <= 0:
        return None
    if person_count > EPAY_MAX_DATA_ROWS:
        raise ValueError("E-pay person set exceeds bounded Sheet layout")
    return {
        "sheetId": sheet_id,
        "startRowIndex": 4,
        "endRowIndex": 4 + person_count,
        "startColumnIndex": 7,
        "endColumnIndex": 9,
    }


def grid_ranges_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("sheetId") != right.get("sheetId"):
        return False

    def bounds(item: dict[str, Any], start: str, end: str) -> tuple[int, int]:
        return int(item.get(start, 0)), int(item.get(end, 2**31 - 1))

    lrs, lre = bounds(left, "startRowIndex", "endRowIndex")
    rrs, rre = bounds(right, "startRowIndex", "endRowIndex")
    lcs, lce = bounds(left, "startColumnIndex", "endColumnIndex")
    rcs, rce = bounds(right, "startColumnIndex", "endColumnIndex")
    return lrs < rre and rrs < lre and lcs < rce and rcs < lce


def _quote_sheet(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


__all__ = [
    "EPAY_FORMAT_VERSION",
    "EPAY_MARKER",
    "EPAY_MAX_DATA_ROWS",
    "EpayLayoutReadback",
    "epay_read_range",
    "epay_write_range",
    "grid_ranges_overlap",
    "grila_unprotected_range",
    "managed_protection_description",
    "parse_epay_readback",
    "preserved_epay_values",
    "render_epay_values",
]
