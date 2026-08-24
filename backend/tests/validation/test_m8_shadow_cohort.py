"""M8 VAL-001 representative shadow cohort contract."""

from tests.fixtures.m8_shadow_cohort import SHADOW_COHORT


REQUIRED_COVERAGE = {
    "BELOW_80",
    "AT_80",
    "BELOW_100",
    "AT_100",
    "BELOW_120",
    "AT_120",
    "EXTRA_HOME",
    "EXTRA_OTHER",
    "BELOW_79",
    "AT_79",
    "NO_DOUBLE_COMMISSION",
    "V2_3457",
    "SIM",
    "EPAY",
    "INCENTIVE",
    "TARGET_ZERO",
    "MISSING_INPUT_BLOCKER_PATH",
    "MID_MONTH_CHANGE",
    "REASSIGNMENT_COMPANY_TOTAL_INVARIANT",
    "PONTAJ_REBUILD",
}

EXPECTED_COMPONENTS = {
    "total_salary",
    "main_commission",
    "main_bonus",
    "extra_fixed_pay",
    "extra_other_commission",
    "sim_commission",
    "epay_commission",
    "progress",
}


def test_shadow_cohort_is_anonymized_and_covers_at_least_eight_stores() -> None:
    assert len(SHADOW_COHORT) >= 8
    assert len({case.store_id for case in SHADOW_COHORT}) >= 8
    assert len({case.case_id for case in SHADOW_COHORT}) == len(SHADOW_COHORT)

    for case in SHADOW_COHORT:
        assert case.store_id.startswith("shadow-store-")
        assert case.inputs.person_id.startswith("shadow-person-")
        assert case.inputs.person_home_store_id == case.store_id
        for day in case.inputs.calendar_days:
            assert day.person_id.startswith("shadow-person-")
            assert day.person_home_store_id.startswith("shadow-store-")
            assert day.store_id is None or day.store_id.startswith("shadow-")


def test_shadow_cohort_declares_required_payroll_and_service_scenarios() -> None:
    actual = set().union(*(case.coverage_tags for case in SHADOW_COHORT))
    assert actual >= REQUIRED_COVERAGE


def test_every_shadow_case_has_a_complete_expected_component_contract() -> None:
    for case in SHADOW_COHORT:
        assert set(case.expected) == EXPECTED_COMPONENTS, case.case_id
