"""Representative anonymized cohort for M8 shadow reconciliation.

This module contains only synthetic identifiers and contract-derived expected
values. It intentionally contains no real store/person names and no Google
resource identifiers. Historical source evidence is selected separately in
``docs/validation/m8-reconciliation-sources.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ugrile.domain.enums import WorkingKind
from ugrile.domain.grid import CalendarGridDay, GridInputs
from ugrile.domain.rule_pack import (
    EpayObservationSnapshot,
    PontajHoursSnapshot,
    RulePackParameters,
)


@dataclass(frozen=True, slots=True)
class ShadowCase:
    case_id: str
    store_id: str
    inputs: GridInputs
    expected: dict[str, Decimal | int]
    coverage_tags: frozenset[str]


def _day(
    *,
    person_id: str,
    home_store_id: str,
    store_id: str,
    day: int,
    sales: str,
    target: str,
    kind: WorkingKind = WorkingKind.NORMAL,
    sim: int = 0,
) -> CalendarGridDay:
    return CalendarGridDay(
        person_id=person_id,
        person_home_store_id=home_store_id,
        business_date=date(2026, 8, day),
        working_kind=kind,
        store_id=store_id,
        target_amount=Decimal(target),
        sales_amount=Decimal(sales),
        sim_quantity=sim,
    )


def _case(
    *,
    case_id: str,
    store_no: int,
    days: tuple[CalendarGridDay, ...],
    expected_total: str,
    expected_main_commission: str = "0",
    expected_main_bonus: str = "0",
    expected_extra_fixed: str = "0",
    expected_extra_other: str = "0",
    expected_sim: str = "0",
    expected_epay: str = "0",
    expected_progress: str = "0",
    salary: str = "2400",
    tickets: str = "480",
    incentive: str = "0",
    flip: str = "0",
    epay_under: int = 0,
    epay_over: int = 0,
    tags: tuple[str, ...] = (),
) -> ShadowCase:
    store_id = f"shadow-store-{store_no:02d}"
    person_id = f"shadow-person-{store_no:02d}"
    inputs = GridInputs(
        person_id=person_id,
        person_home_store_id=store_id,
        parameters=RulePackParameters(
            salary=Decimal(salary),
            tickets=Decimal(tickets),
            flip=Decimal(flip),
            incentive=Decimal(incentive),
        ),
        calendar_days=days,
        epay=EpayObservationSnapshot(
            under_50_quantity=epay_under,
            at_or_over_50_quantity=epay_over,
        ),
        pontaj=PontajHoursSnapshot(
            working_days=len(days),
            working_hours=Decimal(11 * len(days)),
            leave_days=0,
            off_days=31 - len(days),
        ),
    )
    return ShadowCase(
        case_id=case_id,
        store_id=store_id,
        inputs=inputs,
        expected={
            "total_salary": Decimal(expected_total),
            "main_commission": Decimal(expected_main_commission),
            "main_bonus": Decimal(expected_main_bonus),
            "extra_fixed_pay": Decimal(expected_extra_fixed),
            "extra_other_commission": Decimal(expected_extra_other),
            "sim_commission": Decimal(expected_sim),
            "epay_commission": Decimal(expected_epay),
            "progress": Decimal(expected_progress),
        },
        coverage_tags=frozenset(tags),
    )


def _normal_threshold_case(
    *,
    store_no: int,
    case_id: str,
    sales: str,
    expected_total: str,
    expected_commission: str,
    expected_bonus: str,
    expected_progress: str,
    tag: str,
) -> ShadowCase:
    store_id = f"shadow-store-{store_no:02d}"
    person_id = f"shadow-person-{store_no:02d}"
    return _case(
        case_id=case_id,
        store_no=store_no,
        days=(
            _day(
                person_id=person_id,
                home_store_id=store_id,
                store_id=store_id,
                day=1,
                sales=sales,
                target="10000",
            ),
        ),
        expected_total=expected_total,
        expected_main_commission=expected_commission,
        expected_main_bonus=expected_bonus,
        expected_progress=expected_progress,
        tags=("MAIN_THRESHOLD", tag),
    )


def build_shadow_cohort() -> tuple[ShadowCase, ...]:
    """Return the deterministic >=8-store VAL-001 cohort."""

    cases: list[ShadowCase] = [
        _normal_threshold_case(
            store_no=1,
            case_id="main-79.99",
            sales="7999",
            expected_total="2880",
            expected_commission="0",
            expected_bonus="0",
            expected_progress="0.7999",
            tag="BELOW_80",
        ),
        _normal_threshold_case(
            store_no=2,
            case_id="main-80.00",
            sales="8000",
            expected_total="3120",
            expected_commission="240",
            expected_bonus="0",
            expected_progress="0.8000",
            tag="AT_80",
        ),
        _normal_threshold_case(
            store_no=3,
            case_id="main-99.99",
            sales="9999",
            expected_total="3180",
            expected_commission="300",
            expected_bonus="0",
            expected_progress="0.9999",
            tag="BELOW_100",
        ),
        _normal_threshold_case(
            store_no=4,
            case_id="main-100.00",
            sales="10000",
            expected_total="3380",
            expected_commission="300",
            expected_bonus="200",
            expected_progress="1.0000",
            tag="AT_100",
        ),
        _normal_threshold_case(
            store_no=5,
            case_id="main-119.99",
            sales="11999",
            expected_total="3440",
            expected_commission="360",
            expected_bonus="200",
            expected_progress="1.1999",
            tag="BELOW_120",
        ),
        _normal_threshold_case(
            store_no=6,
            case_id="main-120.00",
            sales="12000",
            expected_total="3640",
            expected_commission="360",
            expected_bonus="400",
            expected_progress="1.2000",
            tag="AT_120",
        ),
    ]

    store_id = "shadow-store-07"
    person_id = "shadow-person-07"
    cases.append(
        _case(
            case_id="extra-home-no-double-commission",
            store_no=7,
            days=(
                _day(
                    person_id=person_id,
                    home_store_id=store_id,
                    store_id=store_id,
                    day=1,
                    sales="5000",
                    target="5000",
                ),
                _day(
                    person_id=person_id,
                    home_store_id=store_id,
                    store_id=store_id,
                    day=2,
                    sales="5000",
                    target="5000",
                    kind=WorkingKind.EXTRA_HOME,
                ),
            ),
            expected_total="3530",
            expected_main_commission="300",
            expected_main_bonus="200",
            expected_extra_fixed="150",
            expected_progress="1.0000",
            tags=("EXTRA_HOME", "NO_DOUBLE_COMMISSION"),
        )
    )

    store_id = "shadow-store-08"
    person_id = "shadow-person-08"
    cases.append(
        _case(
            case_id="extra-other-below-79",
            store_no=8,
            days=(
                _day(
                    person_id=person_id,
                    home_store_id=store_id,
                    store_id="shadow-foreign-08",
                    day=3,
                    sales="7800",
                    target="10000",
                    kind=WorkingKind.EXTRA_OTHER,
                ),
            ),
            expected_total="3030",
            expected_extra_fixed="150",
            tags=("EXTRA_OTHER", "BELOW_79"),
        )
    )

    store_id = "shadow-store-09"
    person_id = "shadow-person-09"
    cases.append(
        _case(
            case_id="extra-other-at-79",
            store_no=9,
            days=(
                _day(
                    person_id=person_id,
                    home_store_id=store_id,
                    store_id="shadow-foreign-09",
                    day=4,
                    sales="7900",
                    target="10000",
                    kind=WorkingKind.EXTRA_OTHER,
                ),
            ),
            expected_total="3267",
            expected_extra_fixed="150",
            expected_extra_other="237",
            tags=("EXTRA_OTHER", "AT_79"),
        )
    )

    store_id = "shadow-store-10"
    person_id = "shadow-person-10"
    cases.append(
        _case(
            case_id="v2-3457-sim-incentive",
            store_no=10,
            days=(
                _day(
                    person_id=person_id,
                    home_store_id=store_id,
                    store_id=store_id,
                    day=5,
                    sales="0",
                    target="0",
                    sim=9,
                ),
            ),
            salary="2600",
            tickets="480",
            incentive="350",
            expected_total="3457",
            expected_sim="27",
            tags=("V2_3457", "SIM", "INCENTIVE"),
        )
    )

    store_id = "shadow-store-11"
    person_id = "shadow-person-11"
    cases.append(
        _case(
            case_id="epay-and-target-zero",
            store_no=11,
            days=(
                _day(
                    person_id=person_id,
                    home_store_id=store_id,
                    store_id=store_id,
                    day=6,
                    sales="1000",
                    target="0",
                ),
            ),
            epay_under=10,
            epay_over=10,
            expected_total="3050",
            expected_epay="170",
            tags=("EPAY", "TARGET_ZERO", "MISSING_INPUT_BLOCKER_PATH"),
        )
    )

    store_id = "shadow-store-12"
    person_id = "shadow-person-12"
    cases.append(
        _case(
            case_id="mid-month-reassignment-contract",
            store_no=12,
            days=(
                _day(
                    person_id=person_id,
                    home_store_id=store_id,
                    store_id=store_id,
                    day=10,
                    sales="10000",
                    target="10000",
                ),
                _day(
                    person_id=person_id,
                    home_store_id=store_id,
                    store_id=store_id,
                    day=20,
                    sales="5000",
                    target="5000",
                ),
            ),
            expected_total="3530",
            expected_main_commission="450",
            expected_main_bonus="200",
            expected_progress="1.0000",
            tags=(
                "MID_MONTH_CHANGE",
                "REASSIGNMENT_COMPANY_TOTAL_INVARIANT",
                "PONTAJ_REBUILD",
            ),
        )
    )

    return tuple(cases)


SHADOW_COHORT = build_shadow_cohort()
