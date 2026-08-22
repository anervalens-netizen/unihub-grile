from __future__ import annotations

from ugrile.domain.close_policy import BlockerDisposition, policy_for_rule_pack
from ugrile.domain.enums import CloseBlockerCode, RoleName
from ugrile.domain.errors import ScopeError
from ugrile.domain.grid import GridAnomalyCode
from ugrile.domain.rule_pack import get_default_rule_pack
from ugrile.services.auth import Principal
from ugrile.services.authorization import Capability, authorize, capabilities_for


def _principal(role: RoleName) -> Principal:
    return Principal(
        user_id=f"user_{role.value.lower()}",
        tenant_id="tenant_acme",
        role=role,
        email=f"{role.value.lower()}@example.test",
    )


def test_manager_capabilities_are_read_and_schedule_scoped() -> None:
    principal = _principal(RoleName.MANAGER)
    capabilities = capabilities_for(principal)
    assert Capability.SCHEDULE_READ in capabilities
    assert Capability.SCHEDULE_WRITE in capabilities
    assert Capability.EPAY_READ in capabilities
    assert Capability.SHEET_READ in capabilities
    assert Capability.EXPORT_READ in capabilities
    assert Capability.MONTH_CLOSE not in capabilities
    assert Capability.MONTH_REOPEN not in capabilities
    assert Capability.EPAY_WRITE not in capabilities
    assert Capability.SHEET_SYNC not in capabilities
    assert Capability.EXPORT_CREATE not in capabilities


def test_admin_has_full_capability_set() -> None:
    principal = _principal(RoleName.ADMIN)
    assert capabilities_for(principal) == frozenset(Capability)


def test_authorize_denies_missing_capability() -> None:
    principal = _principal(RoleName.MANAGER)
    try:
        authorize(principal, Capability.MONTH_CLOSE)
    except ScopeError as exc:
        assert exc.details["capability"] == Capability.MONTH_CLOSE.value
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("manager unexpectedly received month.close")


def test_mobiup_close_policy_blocks_fresh_epay_requirement() -> None:
    policy = policy_for_rule_pack(get_default_rule_pack())
    assert policy.rule_pack_version == "mobiup-v1-compat"
    assert (
        policy.disposition(CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED)
        is BlockerDisposition.BLOCKING
    )


def test_projection_and_external_reconciliation_are_not_payroll_truth() -> None:
    policy = policy_for_rule_pack(get_default_rule_pack())
    assert (
        policy.disposition(CloseBlockerCode.SHEET_CANARY_REQUIRED)
        is BlockerDisposition.WARNING
    )
    assert (
        policy.disposition(CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED)
        is BlockerDisposition.WARNING
    )


def test_all_current_grid_anomalies_block_final_close() -> None:
    policy = policy_for_rule_pack(get_default_rule_pack())
    assert all(policy.grid_is_blocking(code) for code in GridAnomalyCode)
    assert (
        policy.disposition(CloseBlockerCode.GRID_CURRENT_REVISION_REQUIRED)
        is BlockerDisposition.BLOCKING
    )
    assert (
        policy.disposition(CloseBlockerCode.GRID_ANOMALY_BLOCKING)
        is BlockerDisposition.BLOCKING
    )
