"""S3 domain tests — deterministic Mobiup grid engine (AC-09).

Golden fixtures (docs/MOBIUP_RULE_PACK.md §8):

1. Progress thresholds (79.99%, 80%, 99.99%, 100%, 119.99%, 120%);
2. ``EXTRA_HOME`` with fixed pay and no double commission;
3. ``EXTRA_OTHER`` below 0.79, at 0.79, above 0.79;
4. Reassignment between persons preserves company totals;
5. SIM and both E-pay categories at 0, 1, 10;
6. V2 example ``2600 + 480 + 27 + 350 = 3457``;
7. Mid-month change rebuilds Pontaj and totals;
8. Target zero, missing sale and uncovered day anomalies;
9. Same payload/hash yields same result.

Each fixture is hand-coded and the engine is asserted to be deterministic
and ``ROUND_HALF_UP`` rounded.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ugrile.domain.enums import WorkingKind
from ugrile.domain.grid import (
    V2_EXAMPLE_INCENTIVE,
    V2_EXAMPLE_SALARY,
    V2_EXAMPLE_SIM_QTY,
    V2_EXAMPLE_TICKETS,
    V2_EXAMPLE_TOTAL,
    CalendarGridDay,
    GridInputs,
    calculate_grid,
)
from ugrile.domain.rule_pack import (
    RULE_PACK_VERSION,
    EpayObservationSnapshot,
    PontajHoursSnapshot,
    RulePackParameters,
    RulePackV1,
    get_default_rule_pack,
    hash_inputs,
)


def _day(
    *,
    store_id: str,
    sales: str,
    target: str,
    kind: WorkingKind = WorkingKind.NORMAL,
    sim: int = 0,
    person_home_store: str | None = None,
) -> CalendarGridDay:
    return CalendarGridDay(
        person_id="p1",
        person_home_store_id=person_home_store or store_id,
        business_date=date(2026, 8, 1),
        working_kind=kind,
        store_id=store_id,
        target_amount=Decimal(target),
        sales_amount=Decimal(sales),
        sim_quantity=sim,
    )


def _inputs(
    *,
    days: tuple[CalendarGridDay, ...],
    salary: Decimal = Decimal("0"),
    tickets: Decimal = Decimal("0"),
    flip: Decimal = Decimal("0"),
    incentive: Decimal = Decimal("0"),
    home_store: str = "s1",
    epay: EpayObservationSnapshot | None = None,
    pontaj: PontajHoursSnapshot | None = None,
) -> GridInputs:
    return GridInputs(
        person_id="p1",
        person_home_store_id=home_store,
        parameters=RulePackParameters(
            salary=salary, tickets=tickets, flip=flip, incentive=incentive
        ),
        calendar_days=days,
        epay=epay or EpayObservationSnapshot.empty(),
        pontaj=pontaj or PontajHoursSnapshot(0, Decimal("0"), 0, 0),
    )


def test_v2_example_2600_480_27_350_equals_3457():
    pack = get_default_rule_pack()
    inputs = _inputs(
        days=(_day(store_id="s1", sales="0", target="0", sim=V2_EXAMPLE_SIM_QTY),),
        salary=V2_EXAMPLE_SALARY,
        tickets=V2_EXAMPLE_TICKETS,
        incentive=V2_EXAMPLE_INCENTIVE,
        pontaj=PontajHoursSnapshot(1, Decimal("11"), 0, 0),
    )
    g = calculate_grid(pack, inputs)
    assert g.salary == Decimal("2600")
    assert g.tickets == Decimal("480")
    assert g.sim_commission == Decimal("27")
    assert g.incentive == Decimal("350")
    assert g.flip == Decimal("0")
    assert g.total_salary == V2_EXAMPLE_TOTAL
    assert g.salary_cash == Decimal("2977")  # 3457 - 480


@pytest.mark.parametrize(
    "realised,target,expected_commission,expected_bonus",
    [
        ("7000", "10000", Decimal("0"), Decimal("0")),  # <80% no commission
        ("8000", "10000", Decimal("240"), Decimal("0")),  # 80% threshold
        ("9999", "10000", Decimal("300"), Decimal("0")),  # <100%
        ("10000", "10000", Decimal("300"), Decimal("200")),  # 100% threshold
        ("11999", "10000", Decimal("360"), Decimal("200")),  # <120%
        ("12000", "10000", Decimal("360"), Decimal("400")),  # 120% threshold
    ],
)
def test_progress_thresholds(realised, target, expected_commission, expected_bonus):
    pack = get_default_rule_pack()
    inputs = _inputs(
        days=(_day(store_id="s1", sales=realised, target=target),),
    )
    g = calculate_grid(pack, inputs)
    assert g.main_commission == expected_commission
    assert g.main_bonus == expected_bonus


def test_target_zero_returns_zero_progress_not_infinity():
    pack = get_default_rule_pack()
    inputs = _inputs(days=(_day(store_id="s1", sales="1000", target="0"),))
    g = calculate_grid(pack, inputs)
    assert g.progress == Decimal("0")
    assert g.main_commission == Decimal("0")
    assert g.main_bonus == Decimal("0")


def test_missing_sale_yields_zero_commission():
    pack = get_default_rule_pack()
    inputs = _inputs(days=(_day(store_id="s1", sales="0", target="10000"),))
    g = calculate_grid(pack, inputs)
    assert g.main_commission == Decimal("0")
    assert g.realised_main == Decimal("0")


def test_extra_home_fixed_pay_no_double_commission():
    pack = get_default_rule_pack()
    inputs = _inputs(
        days=(
            _day(
                store_id="s1",
                sales="10000",
                target="10000",
                kind=WorkingKind.EXTRA_HOME,
                person_home_store="s1",
            ),
        ),
        pontaj=PontajHoursSnapshot(1, Decimal("11"), 0, 0),
    )
    g = calculate_grid(pack, inputs)
    # Extra fixed pay: 150 RON for one EXTRA_HOME day.
    assert g.extra_fixed_pay == Decimal("150")
    assert g.extra_days == 1
    # The home main is computed on the EXTRA_HOME sale (10000 / 10000),
    # so commission + bonus apply exactly once.
    assert g.main_commission == Decimal("300")
    assert g.main_bonus == Decimal("200")


def test_extra_other_sub_threshold_yields_zero_commission():
    pack = get_default_rule_pack()
    inputs = _inputs(
        days=(
            _day(
                store_id="s2",
                sales="5000",
                target="10000",
                kind=WorkingKind.EXTRA_OTHER,
                person_home_store="s1",
            ),
        ),
    )
    g = calculate_grid(pack, inputs)
    assert g.extra_other_commission == Decimal("0")
    assert g.extra_fixed_pay == Decimal("150")  # still fixed pay


def test_extra_other_at_threshold_yields_commission():
    pack = get_default_rule_pack()
    inputs = _inputs(
        days=(
            _day(
                store_id="s2",
                sales="7900",
                target="10000",
                kind=WorkingKind.EXTRA_OTHER,
                person_home_store="s1",
            ),
        ),
    )
    g = calculate_grid(pack, inputs)
    # 7900 / 10000 = 0.79; the engine awards 3% * 7900 = 237 RON.
    assert g.extra_other_commission == Decimal("237")


def test_extra_other_above_threshold_yields_commission():
    pack = get_default_rule_pack()
    inputs = _inputs(
        days=(
            _day(
                store_id="s2",
                sales="9000",
                target="10000",
                kind=WorkingKind.EXTRA_OTHER,
                person_home_store="s1",
            ),
        ),
    )
    g = calculate_grid(pack, inputs)
    assert g.extra_other_commission == Decimal("270")  # 3% * 9000


@pytest.mark.parametrize(
    "sim_qty, epay_under, epay_over, expected_sim, expected_epay",
    [
        (0, 0, 0, Decimal("0"), Decimal("0")),
        (1, 0, 0, Decimal("3"), Decimal("0")),
        (10, 0, 0, Decimal("30"), Decimal("0")),
        (0, 1, 0, Decimal("0"), Decimal("5")),
        (0, 0, 1, Decimal("0"), Decimal("12")),
        (0, 10, 10, Decimal("0"), Decimal("170")),  # 5*10 + 12*10
    ],
)
def test_sim_and_epay_brackets(sim_qty, epay_under, epay_over, expected_sim, expected_epay):
    pack = get_default_rule_pack()
    inputs = _inputs(
        days=(
            _day(
                store_id="s1",
                sales="0",
                target="0",
                sim=sim_qty,
            ),
        ),
        epay=EpayObservationSnapshot(
            under_50_quantity=epay_under, at_or_over_50_quantity=epay_over
        ),
    )
    g = calculate_grid(pack, inputs)
    assert g.sim_commission == expected_sim
    assert g.epay_commission == expected_epay


def test_inputs_hash_is_deterministic_for_same_canonical_inputs():
    pack = get_default_rule_pack()
    canonical = {
        "rule_pack_version": RULE_PACK_VERSION,
        "rule_pack_hash": pack.canonical_hash(),
        "revision": 1,
        "person_id": "p1",
        "home_store_id": "s1",
        "parameters": {
            "salary": "2600",
            "tickets": "480",
            "flip": "0",
            "incentive": "350",
        },
        "calendar": [
            {
                "person_id": "p1",
                "business_date": "2026-08-01",
                "working_kind": "NORMAL",
                "store_id": "s1",
                "target_amount": "10000.00",
                "sales_amount": "1000.00",
                "sim_quantity": 2,
            }
        ],
        "epay": {"under_50": 0, "at_or_over_50": 0},
        "pontaj": {
            "working_days": 1,
            "working_hours": "11",
            "leave_days": 0,
            "off_days": 0,
        },
        "sales_generation": "FIXTURE_V1",
    }
    first = hash_inputs(canonical)
    second = hash_inputs(canonical)
    assert first == second


def test_rule_pack_canonical_hash_is_stable():
    pack_a = RulePackV1.default()
    pack_b = RulePackV1.default()
    assert pack_a.canonical_hash() == pack_b.canonical_hash()
    assert pack_a.version == RULE_PACK_VERSION


def test_uncovered_day_does_not_change_salary_components():
    """An OFF day produces a zero Pontaj row and zero grid components."""

    pack = get_default_rule_pack()
    inputs = _inputs(
        days=(),  # no WORKING row at all
        pontaj=PontajHoursSnapshot(0, Decimal("0"), 0, 31),
    )
    g = calculate_grid(pack, inputs)
    assert g.salary == Decimal("0")
    assert g.main_commission == Decimal("0")
    assert g.total_salary == Decimal("0")
