"""Pure GS-007/GS-008 contracts for E-pay cells and Sheet protection."""

from __future__ import annotations

import copy

import pytest

from ugrile.connectors.google_epay_layout import (
    EPAY_MARKER,
    grila_unprotected_range,
    parse_epay_readback,
    preserved_epay_values,
    render_epay_values,
)
from ugrile.connectors.google_sheet_protection import (
    GoogleProtectionContractError,
    attest_protection_state,
    build_protection_requests,
)

EDITOR = "svc-grile@example.test"


def _empty_state() -> dict[str, object]:
    return {
        "sheets": [
            {
                "properties": {"sheetId": 101, "title": "Grila"},
                "protectedRanges": [],
            },
            {
                "properties": {"sheetId": 202, "title": "Pontaj"},
                "protectedRanges": [],
            },
        ]
    }


def _exact_state(person_count: int = 2) -> dict[str, object]:
    return {
        "sheets": [
            {
                "properties": {"sheetId": 101, "title": "Grila"},
                "protectedRanges": [
                    {
                        "protectedRangeId": 11,
                        "range": {"sheetId": 101},
                        "description": "UGRILE_MANAGED_PROTECTION:v1:Grila",
                        "warningOnly": False,
                        "editors": {
                            "users": [EDITOR],
                            "groups": [],
                            "domainUsersCanEdit": False,
                        },
                        "unprotectedRanges": [
                            {
                                "sheetId": 101,
                                "startRowIndex": 4,
                                "endRowIndex": 4 + person_count,
                                "startColumnIndex": 7,
                                "endColumnIndex": 9,
                            }
                        ]
                        if person_count
                        else [],
                    }
                ],
            },
            {
                "properties": {"sheetId": 202, "title": "Pontaj"},
                "protectedRanges": [
                    {
                        "protectedRangeId": 12,
                        "range": {"sheetId": 202},
                        "description": "UGRILE_MANAGED_PROTECTION:v1:Pontaj",
                        "warningOnly": False,
                        "editors": {
                            "users": [EDITOR],
                            "groups": [],
                            "domainUsersCanEdit": False,
                        },
                        "unprotectedRanges": [],
                    }
                ],
            },
        ]
    }


def test_epay_layout_is_sorted_stable_and_keeps_inputs_outside_projection_columns() -> None:
    rows = render_epay_values(
        month_id="month_aug",
        revision=9,
        person_ids=["person_b", "person_a", "person_a"],
        preserved={"person_a": (3, "2"), "person_b": (0, 10)},
    )

    assert rows == [
        [EPAY_MARKER, "v1", ""],
        ["month_id", "month_aug", ""],
        ["revision", 9, ""],
        ["person_id", "UNDER_50", "AT_OR_OVER_50"],
        ["person_a", 3, "2"],
        ["person_b", 0, 10],
    ]
    assert grila_unprotected_range(sheet_id=101, person_count=2) == {
        "sheetId": 101,
        "startRowIndex": 4,
        "endRowIndex": 6,
        "startColumnIndex": 7,
        "endColumnIndex": 9,
    }


def test_epay_preservation_is_month_bound_and_rejects_ambiguous_identity() -> None:
    same_month = render_epay_values(
        month_id="month_aug",
        revision=1,
        person_ids=["person_a"],
        preserved={"person_a": (4, 5)},
    )
    assert preserved_epay_values(same_month, month_id="month_aug") == {
        "person_a": (4, 5)
    }
    assert preserved_epay_values(same_month, month_id="month_sep") == {}

    duplicate = [*same_month, ["person_a", 9, 9]]
    assert preserved_epay_values(duplicate, month_id="month_aug") == {}


def test_epay_readback_requires_exact_marker_revision_and_person_set() -> None:
    valid = render_epay_values(
        month_id="month_aug",
        revision=7,
        person_ids=["person_a", "person_b"],
        preserved={"person_a": (1, "2"), "person_b": (3.0, 4)},
    )
    parsed = parse_epay_readback(
        valid,
        month_id="month_aug",
        revision=7,
        expected_person_ids=["person_b", "person_a"],
    )
    assert parsed.structure_valid is True
    assert parsed.structural_errors == ()
    assert [item["value"] for item in parsed.observations] == [1, "2", 3.0, 4]

    wrong_revision = copy.deepcopy(valid)
    wrong_revision[2][1] = 6
    failed = parse_epay_readback(
        wrong_revision,
        month_id="month_aug",
        revision=7,
        expected_person_ids=["person_a", "person_b"],
    )
    assert failed.structure_valid is False
    assert "EPAY_LAYOUT_REVISION_MISMATCH" in failed.structural_errors
    assert all(item["value"] is None for item in failed.observations)

    extra_person = [*valid, ["person_c", 1, 1]]
    failed_set = parse_epay_readback(
        extra_person,
        month_id="month_aug",
        revision=7,
        expected_person_ids=["person_a", "person_b"],
    )
    assert failed_set.structure_valid is False
    assert "EPAY_LAYOUT_PERSON_SET_MISMATCH" in failed_set.structural_errors
    assert all(item["value"] is None for item in failed_set.observations)


def test_protection_plan_adds_exact_full_sheet_contract_then_becomes_noop() -> None:
    requests = build_protection_requests(
        _empty_state(),
        grila_tab="Grila",
        pontaj_tab="Pontaj",
        person_count=2,
        editor_email=EDITOR,
    )
    assert len(requests) == 2
    grila = dict(requests[0]["addProtectedRange"])["protectedRange"]
    pontaj = dict(requests[1]["addProtectedRange"])["protectedRange"]
    assert grila["range"] == {"sheetId": 101}
    assert grila["unprotectedRanges"] == [
        {
            "sheetId": 101,
            "startRowIndex": 4,
            "endRowIndex": 6,
            "startColumnIndex": 7,
            "endColumnIndex": 9,
        }
    ]
    assert pontaj["range"] == {"sheetId": 202}
    assert pontaj["unprotectedRanges"] == []

    exact = _exact_state(2)
    assert build_protection_requests(
        exact,
        grila_tab="Grila",
        pontaj_tab="Pontaj",
        person_count=2,
        editor_email=EDITOR,
    ) == ()
    attest_protection_state(
        exact,
        grila_tab="Grila",
        pontaj_tab="Pontaj",
        person_count=2,
        editor_email=EDITOR,
    )


def test_external_protection_is_never_deleted_and_overlap_fails_closed() -> None:
    state = _exact_state(1)
    grila = state["sheets"][0]
    grila["protectedRanges"].append(
        {
            "protectedRangeId": 99,
            "range": {
                "sheetId": 101,
                "startRowIndex": 4,
                "endRowIndex": 5,
                "startColumnIndex": 7,
                "endColumnIndex": 9,
            },
            "description": "external",
            "warningOnly": False,
        }
    )

    with pytest.raises(GoogleProtectionContractError) as excinfo:
        build_protection_requests(
            state,
            grila_tab="Grila",
            pontaj_tab="Pontaj",
            person_count=1,
            editor_email=EDITOR,
        )

    assert excinfo.value.details["code"] == "GOOGLE_EPAY_PROTECTION_CONFLICT"


def test_attestation_rejects_extra_protected_range_editors() -> None:
    state = _exact_state(1)
    state["sheets"][0]["protectedRanges"][0]["editors"]["groups"] = ["ops@example.test"]

    with pytest.raises(GoogleProtectionContractError) as excinfo:
        attest_protection_state(
            state,
            grila_tab="Grila",
            pontaj_tab="Pontaj",
            person_count=1,
            editor_email=EDITOR,
        )

    assert excinfo.value.details["code"] == "GOOGLE_PROTECTION_ATTESTATION_FAILED"
