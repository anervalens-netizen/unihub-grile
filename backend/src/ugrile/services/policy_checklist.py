"""Close-checklist projection backed by the exact final-close policy.

The manager checklist and the final close operation must never disagree about
whether a detected fact is blocking. This projection therefore consumes the
same ``PolicyCloseService`` validation and ``ClosePolicy`` used by final close.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.close_policy import policy_for_rule_pack
from ..domain.enums import CloseBlockerCode, MonthState
from ..domain.rule_pack import get_default_rule_pack
from ..repositories.models import Month, OutboxJob
from .overview import ChecklistItem, CloseChecklist
from .policy_close import PolicyCloseService

_SEVERITY: dict[str, int] = {
    CloseBlockerCode.STORE_DAY_UNCOVERED.value: 1,
    CloseBlockerCode.STORE_DAY_MULTIPLE_WORKING.value: 1,
    CloseBlockerCode.PERSON_DAY_MULTIPLE_WORKING.value: 1,
    CloseBlockerCode.SALES_MISSING_FOR_WORKED_DAY.value: 1,
    CloseBlockerCode.INVALID_WORKING_KIND.value: 2,
    CloseBlockerCode.SALES_ORPHAN_FOR_COVERED_DAY.value: 2,
    CloseBlockerCode.TARGET_ZERO_FOR_WORKED_STORE.value: 2,
    CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED.value: 1,
    CloseBlockerCode.SHEET_CANARY_REQUIRED.value: 3,
    CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED.value: 3,
}

_TITLES: dict[str, str] = {
    CloseBlockerCode.STORE_DAY_UNCOVERED.value: "Magazin fără agent într-o zi",
    CloseBlockerCode.STORE_DAY_MULTIPLE_WORKING.value: "Magazin cu doi agenți într-o zi",
    CloseBlockerCode.PERSON_DAY_MULTIPLE_WORKING.value: "Agent în două magazine într-o zi",
    CloseBlockerCode.INVALID_WORKING_KIND.value: "Clasificare invalidă (home/other)",
    CloseBlockerCode.SALES_MISSING_FOR_WORKED_DAY.value: "Vânzare lipsă pentru o zi lucrată",
    CloseBlockerCode.SALES_ORPHAN_FOR_COVERED_DAY.value: "Vânzare fără calendar",
    CloseBlockerCode.TARGET_ZERO_FOR_WORKED_STORE.value: "Target lipsă/zero pentru magazin lucrat",
    CloseBlockerCode.EPAY_FRESH_READBACK_REQUIRED.value: "E-pay incomplet sau expirat",
    CloseBlockerCode.SHEET_CANARY_REQUIRED.value: "Verificare Sheet necesară",
    CloseBlockerCode.EXTERNAL_RECONCILIATION_REQUIRED.value: "Reconciliere externă necesară",
}


class PolicyCloseChecklistService:
    """Read model for the close UI using final-close validation semantics."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def month_checklist(self, *, tenant_id: str, month: Month) -> CloseChecklist:
        validation = PolicyCloseService(self.session)._validation(
            tenant_id=tenant_id,
            month=month,
        )
        policy = policy_for_rule_pack(get_default_rule_pack())
        items = [
            ChecklistItem(
                code=blocker.code.value,
                severity=_SEVERITY.get(blocker.code.value, 1),
                title=_TITLES.get(blocker.code.value, blocker.code.value),
                detail=blocker.message,
                blocking=policy.is_blocking(blocker.code),
            )
            for blocker in validation.blockers
        ]
        items.sort(key=lambda item: (not item.blocking, item.severity, item.code, item.detail))

        job_rows = list(
            self.session.execute(
                select(OutboxJob)
                .where(
                    OutboxJob.tenant_id == tenant_id,
                    OutboxJob.kind.in_(
                        [
                            "EXPORT_XLSX_STORE",
                            "EXPORT_XLSX_BULK",
                            "EXPORT_PONTAJ_ONLY",
                            "GOOGLE_PROJECTION_STORE",
                        ]
                    ),
                )
                .order_by(OutboxJob.id.desc())
                .limit(20)
            ).scalars()
        )
        job_summary: list[dict[str, Any]] = [
            {
                "job_id": row.id,
                "kind": row.kind,
                "status": row.status,
                "idempotency_key": row.idempotency_key,
                "attempts": row.attempts,
                "last_error": row.last_error,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in job_rows
        ]
        return CloseChecklist(
            month_id=month.id,
            revision=month.revision,
            state=MonthState(month.state),
            blockers=tuple(items),
            generated_at=None,
            export_summary=(),
            job_summary=tuple(job_summary),
            expected_revision=month.revision,
        )


__all__ = ["PolicyCloseChecklistService"]
