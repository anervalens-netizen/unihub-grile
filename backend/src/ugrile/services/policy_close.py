"""Financial close orchestration layered on the legacy close snapshot builder.

This service keeps the proven transaction/locking/audit implementation from
``CloseService`` but replaces the former stage-era deferred-blocker filtering
with the versioned close policy. E-pay freshness is evaluated from persisted
valid observations for every active store and blocks close when the active rule
pack pays E-pay commission.
"""

from __future__ import annotations

from sqlalchemy import select

from ..domain.close import BlockerDetail, CloseValidation
from ..domain.close_policy import policy_for_rule_pack
from ..domain.enums import CloseBlockerCode
from ..domain.rule_pack import get_default_rule_pack
from ..repositories.models import Month, Store
from .close import CloseService
from .epay import freshness_for_month


class PolicyCloseService(CloseService):
    """CloseService with policy-driven financial blocker enforcement."""

    def _validation(self, *, tenant_id: str, month: Month) -> CloseValidation:
        base = super()._validation(tenant_id=tenant_id, month=month)
        # Replace the generic historical E-pay placeholder with precise
        # store-scoped freshness facts. Keep other operational deferred facts
        # visible for audit/checklist compatibility.
        blockers = [
            blocker
            for blocker in base.blockers
            if blocker.code is not CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED
        ]
        active_store_ids = list(
            self.session.execute(
                select(Store.id).where(
                    Store.tenant_id == tenant_id,
                    Store.is_active.is_(True),
                )
            ).scalars()
        )
        for store_id in sorted(active_store_ids):
            report = freshness_for_month(
                self.session,
                tenant_id=tenant_id,
                store_id=store_id,
                month_id=month.id,
            )
            if report.is_fresh:
                continue
            blockers.append(
                BlockerDetail(
                    code=CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED,
                    store_id=store_id,
                    person_id=None,
                    business_date=None,
                    message=(
                        f"store {store_id} E-pay readback is incomplete/stale: "
                        f"{report.fresh_count}/{report.expected_count} fresh pairs"
                    ),
                )
            )
        blockers.sort(
            key=lambda b: (
                b.code.value,
                b.store_id or "",
                b.person_id or "",
                b.business_date.isoformat() if b.business_date else "",
            )
        )
        return CloseValidation(blockers=tuple(blockers))

    @staticmethod
    def _enforced_blockers(validation: CloseValidation) -> tuple[BlockerDetail, ...]:
        policy = policy_for_rule_pack(get_default_rule_pack())
        return tuple(blocker for blocker in validation.blockers if policy.is_blocking(blocker.code))


__all__ = ["PolicyCloseService"]
