"""Google Sheet protection planning and attestation for managed Grila/Pontaj tabs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..domain.errors import DomainError
from .google_epay_layout import (
    grid_ranges_overlap,
    grila_unprotected_range,
    managed_protection_description,
)


class GoogleProtectionContractError(DomainError):
    """Protection state cannot safely satisfy the editable-cell contract."""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _sheet_entries(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [_mapping(item) for item in _list(state.get("sheets"))]


def _sheet_by_title(state: Mapping[str, Any], title: str) -> dict[str, Any]:
    matches = [
        sheet
        for sheet in _sheet_entries(state)
        if _mapping(sheet.get("properties")).get("title") == title
    ]
    if len(matches) != 1:
        raise GoogleProtectionContractError(
            "Google Sheet binding tab could not be resolved uniquely",
            details={"code": "GOOGLE_SHEET_TAB_NOT_FOUND", "tab": title},
        )
    return matches[0]


def _sheet_id(sheet: Mapping[str, Any], *, title: str) -> int:
    value = _mapping(sheet.get("properties")).get("sheetId")
    if type(value) is not int:
        raise GoogleProtectionContractError(
            "Google Sheet tab has no numeric sheet id",
            details={"code": "GOOGLE_SHEET_CONTROL_STATE_INVALID", "tab": title},
        )
    return value


def _protected_ranges(sheet: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [_mapping(item) for item in _list(sheet.get("protectedRanges"))]


def _expected_protection(
    *,
    sheet_id: int,
    tab_name: str,
    editor_email: str,
    unprotected: dict[str, int] | None,
) -> dict[str, Any]:
    return {
        "range": {"sheetId": sheet_id},
        "description": managed_protection_description(tab_name),
        "warningOnly": False,
        "editors": {
            "users": [editor_email],
            "groups": [],
            "domainUsersCanEdit": False,
        },
        "unprotectedRanges": [unprotected] if unprotected is not None else [],
    }


def _managed_ranges(sheet: Mapping[str, Any], tab_name: str) -> list[dict[str, Any]]:
    description = managed_protection_description(tab_name)
    return [
        item for item in _protected_ranges(sheet) if item.get("description") == description
    ]


def _assert_no_input_conflict(
    sheet: Mapping[str, Any],
    *,
    tab_name: str,
    input_range: dict[str, int] | None,
) -> None:
    if input_range is None:
        return
    managed_description = managed_protection_description(tab_name)
    for protected in _protected_ranges(sheet):
        if protected.get("description") == managed_description:
            continue
        if protected.get("warningOnly") is True:
            continue
        if protected.get("namedRangeId"):
            raise GoogleProtectionContractError(
                "an external named-range protection prevents proving E-pay editability",
                details={"code": "GOOGLE_EPAY_PROTECTION_CONFLICT", "tab": tab_name},
            )
        protected_range = _mapping(protected.get("range"))
        if protected_range and grid_ranges_overlap(protected_range, input_range):
            raise GoogleProtectionContractError(
                "an external protected range overlaps the E-pay input cells",
                details={"code": "GOOGLE_EPAY_PROTECTION_CONFLICT", "tab": tab_name},
            )


def _normalized_grid_range(value: Any) -> dict[str, int]:
    raw = _mapping(value)
    result: dict[str, int] = {}
    for key in (
        "sheetId",
        "startRowIndex",
        "endRowIndex",
        "startColumnIndex",
        "endColumnIndex",
    ):
        item = raw.get(key)
        if type(item) is int:
            result[key] = item
    return result


def _normalized_unprotected(value: Any) -> list[dict[str, int]]:
    return [_normalized_grid_range(item) for item in _list(value)]


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _matches_expected(
    protected: Mapping[str, Any],
    *,
    expected: Mapping[str, Any],
    editor_email: str,
    owner_emails: frozenset[str],
) -> bool:
    if protected.get("description") != expected.get("description"):
        return False
    if _normalized_grid_range(protected.get("range")) != _normalized_grid_range(
        expected.get("range")
    ):
        return False
    if protected.get("warningOnly") is True:
        return False
    editors = _mapping(protected.get("editors"))
    expected_editors = frozenset({editor_email, *owner_emails})
    if frozenset(_text_list(editors.get("users"))) != expected_editors:
        return False
    if _text_list(editors.get("groups")):
        return False
    if editors.get("domainUsersCanEdit") is True:
        return False
    return _normalized_unprotected(protected.get("unprotectedRanges")) == (
        _normalized_unprotected(expected.get("unprotectedRanges"))
    )


def build_protection_requests(
    state: Mapping[str, Any],
    *,
    grila_tab: str,
    pontaj_tab: str,
    person_count: int,
    editor_email: str,
    owner_emails: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], ...]:
    """Return only material add/update/delete requests for managed protections."""

    if not editor_email:
        raise GoogleProtectionContractError(
            "managed protection requires the service-account editor identity",
            details={"code": "GOOGLE_PROTECTION_EDITOR_REQUIRED"},
        )
    requests: list[dict[str, Any]] = []
    for tab_name, allow_epay in ((grila_tab, True), (pontaj_tab, False)):
        sheet = _sheet_by_title(state, tab_name)
        sheet_id = _sheet_id(sheet, title=tab_name)
        unprotected = (
            grila_unprotected_range(sheet_id=sheet_id, person_count=person_count)
            if allow_epay
            else None
        )
        if allow_epay:
            _assert_no_input_conflict(
                sheet,
                tab_name=tab_name,
                input_range=unprotected,
            )
        expected = _expected_protection(
            sheet_id=sheet_id,
            tab_name=tab_name,
            editor_email=editor_email,
            unprotected=unprotected,
        )
        managed = _managed_ranges(sheet, tab_name)
        if managed:
            first_id = managed[0].get("protectedRangeId")
            if type(first_id) is not int:
                raise GoogleProtectionContractError(
                    "managed protected range has no numeric id",
                    details={"code": "GOOGLE_SHEET_CONTROL_STATE_INVALID", "tab": tab_name},
                )
            if not _matches_expected(
                managed[0],
                expected=expected,
                editor_email=editor_email,
                owner_emails=owner_emails,
            ):
                requests.append(
                    {
                        "updateProtectedRange": {
                            "protectedRange": {**expected, "protectedRangeId": first_id},
                            "fields": (
                                "range,description,warningOnly,editors,unprotectedRanges"
                            ),
                        }
                    }
                )
            for duplicate in managed[1:]:
                duplicate_id = duplicate.get("protectedRangeId")
                if type(duplicate_id) is not int:
                    raise GoogleProtectionContractError(
                        "duplicate managed protection has no numeric id",
                        details={
                            "code": "GOOGLE_SHEET_CONTROL_STATE_INVALID",
                            "tab": tab_name,
                        },
                    )
                requests.append(
                    {"deleteProtectedRange": {"protectedRangeId": duplicate_id}}
                )
        else:
            requests.append({"addProtectedRange": {"protectedRange": expected}})
    return tuple(requests)


def attest_protection_state(
    state: Mapping[str, Any],
    *,
    grila_tab: str,
    pontaj_tab: str,
    person_count: int,
    editor_email: str,
    owner_emails: frozenset[str] = frozenset(),
) -> None:
    """Require one exact managed protection per tab and no E-pay overlap conflict."""

    for tab_name, allow_epay in ((grila_tab, True), (pontaj_tab, False)):
        sheet = _sheet_by_title(state, tab_name)
        sheet_id = _sheet_id(sheet, title=tab_name)
        unprotected = (
            grila_unprotected_range(sheet_id=sheet_id, person_count=person_count)
            if allow_epay
            else None
        )
        if allow_epay:
            _assert_no_input_conflict(
                sheet,
                tab_name=tab_name,
                input_range=unprotected,
            )
        managed = _managed_ranges(sheet, tab_name)
        if len(managed) != 1:
            raise GoogleProtectionContractError(
                "managed Sheet protection is missing or duplicated",
                details={"code": "GOOGLE_PROTECTION_ATTESTATION_FAILED", "tab": tab_name},
            )
        expected = _expected_protection(
            sheet_id=sheet_id,
            tab_name=tab_name,
            editor_email=editor_email,
            unprotected=unprotected,
        )
        if not _matches_expected(
            managed[0],
            expected=expected,
            editor_email=editor_email,
            owner_emails=owner_emails,
        ):
            raise GoogleProtectionContractError(
                "managed protection does not exactly match the editable-cell contract",
                details={"code": "GOOGLE_PROTECTION_ATTESTATION_FAILED", "tab": tab_name},
            )


__all__ = [
    "GoogleProtectionContractError",
    "attest_protection_state",
    "build_protection_requests",
]
