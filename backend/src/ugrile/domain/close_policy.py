"""Versioned close policy for financial and operational blockers.

Close validation detects facts; this policy decides which facts may prevent a
final payroll close for the active rule pack. The distinction is explicit so a
projection/operational warning can never accidentally bypass a financial input.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .enums import CloseBlockerCode
from .grid import GridAnomalyCode
from .rule_pack import RulePackV1


class BlockerDisposition(StrEnum):
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"


@dataclass(frozen=True, slots=True)
class ClosePolicy:
    """Immutable close policy tied to one rule-pack version."""

    version: str
    rule_pack_version: str
    close_blockers: dict[CloseBlockerCode, BlockerDisposition]
    grid_anomalies: dict[GridAnomalyCode, BlockerDisposition]

    def disposition(self, code: CloseBlockerCode) -> BlockerDisposition:
        return self.close_blockers.get(code, BlockerDisposition.BLOCKING)

    def grid_disposition(self, code: GridAnomalyCode) -> BlockerDisposition:
        return self.grid_anomalies.get(code, BlockerDisposition.BLOCKING)

    def is_blocking(self, code: CloseBlockerCode) -> bool:
        return self.disposition(code) is BlockerDisposition.BLOCKING

    def grid_is_blocking(self, code: GridAnomalyCode) -> bool:
        return self.grid_disposition(code) is BlockerDisposition.BLOCKING


def policy_for_rule_pack(pack: RulePackV1) -> ClosePolicy:
    """Build the current policy from rule-pack semantics.

    E-pay is financially relevant whenever either E-pay unit rate is non-zero,
    therefore fresh complete month-bound readback is a hard close condition.
    A final close also requires a grid snapshot for the exact current revision
    and fails closed on every current payroll-significant grid anomaly. Google
    Sheet canary/reconciliation are operational/server-test evidence and do not
    alter PostgreSQL payroll truth, so they remain visible warnings.
    """

    epay_required = pack.epay_under_50_rate != 0 or pack.epay_at_or_over_50_rate != 0
    close_blockers = {
        CloseBlockerCode.STORE_DAY_UNCOVERED: BlockerDisposition.BLOCKING,
        CloseBlockerCode.STORE_DAY_MULTIPLE_WORKING: BlockerDisposition.BLOCKING,
        CloseBlockerCode.PERSON_DAY_MULTIPLE_WORKING: BlockerDisposition.BLOCKING,
        CloseBlockerCode.INVALID_WORKING_KIND: BlockerDisposition.BLOCKING,
        CloseBlockerCode.SALES_MISSING_FOR_WORKED_DAY: BlockerDisposition.BLOCKING,
        CloseBlockerCode.SALES_ORPHAN_FOR_COVERED_DAY: BlockerDisposition.BLOCKING,
        CloseBlockerCode.TARGET_ZERO_FOR_WORKED_STORE: BlockerDisposition.BLOCKING,
        CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED: (
            BlockerDisposition.BLOCKING if epay_required else BlockerDisposition.WARNING
        ),
        CloseBlockerCode.GRID_CURRENT_REVISION_REQUIRED: BlockerDisposition.BLOCKING,
        CloseBlockerCode.GRID_ANOMALY_BLOCKING: BlockerDisposition.BLOCKING,
        CloseBlockerCode.SHEET_CANARY_REQUIRED: BlockerDisposition.WARNING,
        CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED: BlockerDisposition.WARNING,
    }
    grid_anomalies = {code: BlockerDisposition.BLOCKING for code in GridAnomalyCode}
    return ClosePolicy(
        version="close-policy-v1",
        rule_pack_version=pack.version,
        close_blockers=close_blockers,
        grid_anomalies=grid_anomalies,
    )


__all__ = [
    "BlockerDisposition",
    "ClosePolicy",
    "policy_for_rule_pack",
]
