from __future__ import annotations

import json

from ugrile.domain.enums import CloseBlockerCode, MonthState
from ugrile.domain.grid import GridAnomalyCode
from ugrile.domain.rule_pack import RULE_PACK_VERSION
from ugrile.repositories.models import GridCalculation
from ugrile.repositories.months import MonthRepository
from ugrile.services.policy_close import PolicyCloseService


def _month(session, faker_tenant):
    month = MonthRepository(session).get_or_create(
        faker_tenant["tenant_id"], 2026, 8
    )
    month.state = MonthState.OPEN
    session.commit()
    return month


def _grid_row(*, tenant_id: str, month_id: str, revision: int, store_id: str, person_id: str, anomalies=None, epay=(0, 0)):
    payload = json.dumps(
        {
            "inputs": {
                "epay": {
                    "under_50": epay[0],
                    "at_or_over_50": epay[1],
                }
            },
            "components": {},
            "anomalies": anomalies or [],
        },
        sort_keys=True,
    )
    return GridCalculation(
        tenant_id=tenant_id,
        month_id=month_id,
        store_id=store_id,
        person_id=person_id,
        rule_pack_version=RULE_PACK_VERSION,
        revision=revision,
        inputs_hash="a" * 64,
        outputs_hash="b" * 64,
        payload=payload,
    )


def _seed_current_grid(session, faker_tenant, month, *, person_a_anomalies=None, person_a_epay=(0, 0)):
    session.add_all(
        [
            _grid_row(
                tenant_id=faker_tenant["tenant_id"],
                month_id=month.id,
                revision=month.revision,
                store_id=faker_tenant["store_id"],
                person_id=faker_tenant["person_a_id"],
                anomalies=person_a_anomalies,
                epay=person_a_epay,
            ),
            _grid_row(
                tenant_id=faker_tenant["tenant_id"],
                month_id=month.id,
                revision=month.revision,
                store_id=faker_tenant["store_id"],
                person_id=faker_tenant["person_b_id"],
            ),
            _grid_row(
                tenant_id=faker_tenant["tenant_id"],
                month_id=month.id,
                revision=month.revision,
                store_id=faker_tenant["other_store_id"],
                person_id=faker_tenant["person_c_id"],
            ),
        ]
    )
    session.commit()


def test_final_close_policy_requires_exact_current_grid_for_every_active_person(session, faker_tenant):
    month = _month(session, faker_tenant)
    service = PolicyCloseService(session)

    blockers = service._grid_blockers(
        tenant_id=faker_tenant["tenant_id"],
        month=month,
    )

    assert len(blockers) == 3
    assert {blocker.code for blocker in blockers} == {
        CloseBlockerCode.GRID_CURRENT_REVISION_REQUIRED
    }
    enforced = service._enforced_blockers(
        service._validation(tenant_id=faker_tenant["tenant_id"], month=month)
    )
    assert CloseBlockerCode.GRID_CURRENT_REVISION_REQUIRED in {
        blocker.code for blocker in enforced
    }


def test_final_close_policy_enforces_persisted_grid_anomaly(session, faker_tenant):
    month = _month(session, faker_tenant)
    _seed_current_grid(
        session,
        faker_tenant,
        month,
        person_a_anomalies=[
            {
                "code": GridAnomalyCode.SALARY_MASTER_MISSING.value,
                "store_id": faker_tenant["store_id"],
                "person_id": faker_tenant["person_a_id"],
                "business_date": None,
                "message": "salary master missing",
            }
        ],
    )

    blockers = PolicyCloseService(session)._grid_blockers(
        tenant_id=faker_tenant["tenant_id"],
        month=month,
    )

    anomaly_blockers = [
        blocker
        for blocker in blockers
        if blocker.code is CloseBlockerCode.GRID_ANOMALY_BLOCKING
    ]
    assert len(anomaly_blockers) == 1
    assert GridAnomalyCode.SALARY_MASTER_MISSING.value in anomaly_blockers[0].message


def test_grid_epay_inputs_must_match_month_bound_snapshot(session, faker_tenant):
    month = _month(session, faker_tenant)
    _seed_current_grid(
        session,
        faker_tenant,
        month,
        person_a_epay=(9, 9),
    )

    blockers = PolicyCloseService(session)._grid_blockers(
        tenant_id=faker_tenant["tenant_id"],
        month=month,
    )

    mismatches = [
        blocker
        for blocker in blockers
        if blocker.code is CloseBlockerCode.GRID_CURRENT_REVISION_REQUIRED
        and blocker.person_id == faker_tenant["person_a_id"]
        and "E-pay inputs" in blocker.message
    ]
    assert len(mismatches) == 1
