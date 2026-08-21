"""S5a tests — typed S4/S5 deferred blockers surface in the close lattice.

The S3 close lattice already accepts an ``extra_blockers`` parameter on
:func:`ugrile.domain.close.validate_close`. The S5a slice adds the
informational-only list of typed codes that integration stages wire
next; the unit tests confirm they appear in the returned
:class:`CloseValidation` without bypassing any existing check.
"""

from __future__ import annotations

from datetime import date

from ugrile.domain.close import (
    TYPED_DEFERRED_BLOCKERS,
    CloseValidation,
    OpenStoreDay,
    deferred_blockers,
    validate_close,
)
from ugrile.domain.enums import CloseBlockerCode


def test_deferred_blockers_returns_typed_codes():
    codes = deferred_blockers()
    assert CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED in codes
    assert CloseBlockerCode.SHEET_CANARY_REQUIRED in codes
    assert CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED in codes


def test_typed_deferred_blockers_tuple_is_frozen():
    # The tuple is frozen at module level so the integration stages can
    # import it without copying. Confirm it contains the three S5a
    # blockers.
    assert set(TYPED_DEFERRED_BLOCKERS) == {
        CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED,
        CloseBlockerCode.SHEET_CANARY_REQUIRED,
        CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED,
    }


def test_validate_close_surfaces_typed_deferred_blockers():
    """Passing the deferred codes via ``extra_blockers`` produces typed
    ``BlockerDetail`` rows with the canonical message."""

    validation = validate_close(
        open_days=[],
        coverage=[],
        person_days=[],
        sales_availability=[],
        target_availability=[],
        extra_blockers=deferred_blockers(),
    )
    codes = {blocker.code for blocker in validation.blockers}
    assert codes == {
        CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED,
        CloseBlockerCode.SHEET_CANARY_REQUIRED,
        CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED,
    }
    # Each blocker carries the canonical "deferred blocker" message
    # (the only allowed phrasing at S5a — real messages land in S5b/S6).
    for blocker in validation.blockers:
        assert blocker.message.startswith("deferred blocker:")
        assert blocker.business_date is None
        assert blocker.store_id is None
        assert blocker.person_id is None


def test_validate_close_omits_typed_blockers_when_not_requested():
    validation = validate_close(
        open_days=[],
        coverage=[],
        person_days=[],
        sales_availability=[],
        target_availability=[],
    )
    assert validation == CloseValidation(blockers=())


def test_validate_close_keeps_lattice_blockers_with_typed_deferred():
    """An open-store/day lattice with no assignment still raises the
    typed blockers deterministically alongside the informational codes.
    """

    validation = validate_close(
        open_days=[
            OpenStoreDay(store_id="store_x", business_date=date(2026, 8, 1)),
            OpenStoreDay(store_id="store_x", business_date=date(2026, 8, 2)),
        ],
        coverage=[],
        person_days=[],
        sales_availability=[],
        target_availability=[],
        extra_blockers=deferred_blockers(),
    )
    codes = [blocker.code for blocker in validation.blockers]
    # The lattice blockers come first by sort key, then the deferred codes.
    assert codes.count(CloseBlockerCode.STORE_DAY_UNCOVERED) == 2
    assert CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED in codes
    assert CloseBlockerCode.SHEET_CANARY_REQUIRED in codes
    assert CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED in codes
