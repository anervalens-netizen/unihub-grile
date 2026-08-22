"""Financial close orchestration layered on the close snapshot builder.

Final close requires fresh month-bound E-pay plus one valid grid snapshot for
every historical payroll participant at the exact current month revision.
Current ``Person.is_active`` is not historical truth: a leaver with month
participation remains part of payroll and therefore remains a close obligation.
"""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select

from ..domain.close import BlockerDetail, CloseValidation
from ..domain.close_policy import ClosePolicy, policy_for_rule_pack
from ..domain.enums import CloseBlockerCode
from ..domain.grid import GridAnomalyCode
from ..domain.rule_pack import RULE_PACK_VERSION, get_default_rule_pack
from ..repositories.epay import latest_snapshot
from ..repositories.models import GridCalculation, Month, Person, Store
from .close import CloseService
from .epay import freshness_for_month
from .financial_inputs import financial_input_mismatch
from .month_participants import month_participant_ids


class PolicyCloseService(CloseService):
    """CloseService with policy-driven financial blocker enforcement."""

    def _validation(self, *, tenant_id: str, month: Month) -> CloseValidation:
        base = super()._validation(tenant_id=tenant_id, month=month)
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
                        f"store {store_id} E-pay readback is incomplete/stale for {month.id}: "
                        f"{report.fresh_count}/{report.expected_count} fresh pairs"
                    ),
                )
            )

        blockers.extend(self._grid_blockers(tenant_id=tenant_id, month=month))
        blockers.sort(
            key=lambda blocker: (
                blocker.code.value,
                blocker.store_id or "",
                blocker.person_id or "",
                blocker.business_date.isoformat() if blocker.business_date else "",
                blocker.message,
            )
        )
        return CloseValidation(blockers=tuple(blockers))

    def _grid_blockers(self, *, tenant_id: str, month: Month) -> list[BlockerDetail]:
        """Validate grid completeness for every historical month participant."""

        policy = policy_for_rule_pack(get_default_rule_pack())
        participant_ids = month_participant_ids(
            self.session,
            tenant_id=tenant_id,
            month=month,
        )
        people = (
            list(
                self.session.execute(
                    select(Person).where(
                        Person.tenant_id == tenant_id,
                        Person.id.in_(participant_ids),
                    )
                ).scalars()
            )
            if participant_ids
            else []
        )
        rows = list(
            self.session.execute(
                select(GridCalculation).where(
                    GridCalculation.tenant_id == tenant_id,
                    GridCalculation.month_id == month.id,
                    GridCalculation.revision == month.revision,
                    GridCalculation.rule_pack_version == RULE_PACK_VERSION,
                )
            ).scalars()
        )
        rows_by_person: dict[str, list[GridCalculation]] = {}
        for row in rows:
            rows_by_person.setdefault(row.person_id, []).append(row)

        blockers: list[BlockerDetail] = []
        for person in sorted(people, key=lambda item: item.id):
            person_rows = rows_by_person.get(person.id, [])
            if len(person_rows) != 1:
                blockers.append(
                    BlockerDetail(
                        code=CloseBlockerCode.GRID_CURRENT_REVISION_REQUIRED,
                        store_id=person.home_store_id,
                        person_id=person.id,
                        business_date=None,
                        message=(
                            f"person {person.id} requires exactly one {RULE_PACK_VERSION} grid row "
                            f"for revision {month.revision}; found {len(person_rows)}"
                        ),
                    )
                )
                continue

            row = person_rows[0]
            if row.store_id != person.home_store_id:
                blockers.append(
                    BlockerDetail(
                        code=CloseBlockerCode.GRID_CURRENT_REVISION_REQUIRED,
                        store_id=row.store_id,
                        person_id=person.id,
                        business_date=None,
                        message=(
                            f"grid home store {row.store_id} does not match current home store "
                            f"{person.home_store_id} for person {person.id}"
                        ),
                    )
                )

            try:
                payload = json.loads(row.payload)
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = None
            if not isinstance(payload, dict):
                blockers.append(
                    self._invalid_grid_payload(row, "grid payload is not a JSON object")
                )
                continue

            anomalies = payload.get("anomalies")
            if not isinstance(anomalies, list):
                blockers.append(
                    self._invalid_grid_payload(row, "grid payload has no anomaly list")
                )
            else:
                for anomaly in anomalies:
                    blocker = self._grid_anomaly_blocker(row, anomaly, policy)
                    if blocker is not None:
                        blockers.append(blocker)

            financial_mismatch = financial_input_mismatch(
                self.session,
                tenant_id=tenant_id,
                month=month,
                person=person,
                row=row,
            )
            if financial_mismatch is not None:
                blockers.append(
                    BlockerDetail(
                        code=CloseBlockerCode.GRID_CURRENT_REVISION_REQUIRED,
                        store_id=row.store_id,
                        person_id=row.person_id,
                        business_date=None,
                        message=f"grid financial inputs are stale: {financial_mismatch}",
                    )
                )

            inputs = payload.get("inputs")
            epay_inputs = inputs.get("epay") if isinstance(inputs, dict) else None
            expected_epay = latest_snapshot(
                self.session,
                tenant_id=tenant_id,
                month_id=month.id,
                store_id=person.home_store_id,
                person_id=person.id,
            )
            expected_pair = (
                expected_epay.under_50_quantity,
                expected_epay.at_or_over_50_quantity,
            )
            actual_pair: tuple[int, int] | None = None
            if isinstance(epay_inputs, dict):
                under = epay_inputs.get("under_50")
                over = epay_inputs.get("at_or_over_50")
                if isinstance(under, int) and isinstance(over, int):
                    actual_pair = (under, over)
            if actual_pair != expected_pair:
                blockers.append(
                    BlockerDetail(
                        code=CloseBlockerCode.GRID_CURRENT_REVISION_REQUIRED,
                        store_id=row.store_id,
                        person_id=row.person_id,
                        business_date=None,
                        message=(
                            "grid E-pay inputs do not match the accepted month-bound snapshot: "
                            f"grid={actual_pair}, current={expected_pair}"
                        ),
                    )
                )
        return blockers

    @staticmethod
    def _invalid_grid_payload(row: GridCalculation, message: str) -> BlockerDetail:
        return BlockerDetail(
            code=CloseBlockerCode.GRID_CURRENT_REVISION_REQUIRED,
            store_id=row.store_id,
            person_id=row.person_id,
            business_date=None,
            message=message,
        )

    @staticmethod
    def _grid_anomaly_blocker(
        row: GridCalculation,
        anomaly: object,
        policy: ClosePolicy,
    ) -> BlockerDetail | None:
        if not isinstance(anomaly, dict):
            return PolicyCloseService._invalid_grid_payload(
                row,
                "grid anomaly entry is not an object",
            )
        code_value = anomaly.get("code")
        try:
            anomaly_code = GridAnomalyCode(str(code_value))
        except ValueError:
            return BlockerDetail(
                code=CloseBlockerCode.GRID_ANOMALY_BLOCKING,
                store_id=str(anomaly.get("store_id") or row.store_id),
                person_id=str(anomaly.get("person_id") or row.person_id),
                business_date=PolicyCloseService._parse_business_date(
                    anomaly.get("business_date")
                ),
                message=f"unknown grid anomaly fails closed: {code_value}",
            )
        if not policy.grid_is_blocking(anomaly_code):
            return None
        return BlockerDetail(
            code=CloseBlockerCode.GRID_ANOMALY_BLOCKING,
            store_id=str(anomaly.get("store_id") or row.store_id),
            person_id=str(anomaly.get("person_id") or row.person_id),
            business_date=PolicyCloseService._parse_business_date(
                anomaly.get("business_date")
            ),
            message=(
                f"blocking grid anomaly {anomaly_code.value}: "
                f"{(anomaly.get('message') or 'no detail')!s}"
            ),
        )

    @staticmethod
    def _parse_business_date(value: object) -> date | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _enforced_blockers(validation: CloseValidation) -> tuple[BlockerDetail, ...]:
        policy = policy_for_rule_pack(get_default_rule_pack())
        return tuple(
            blocker
            for blocker in validation.blockers
            if policy.is_blocking(blocker.code)
        )


__all__ = ["PolicyCloseService"]
