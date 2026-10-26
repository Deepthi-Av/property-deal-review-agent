from decimal import Decimal

import pytest

from home_review.checks import apply_clarifications, audit_source_coverage, compare_deals, summarize_deal, validate_facts
from home_review.models import Fact, Limits


def check(case, key="current", monthly="3300", cash="95000"):
    return summarize_deal(getattr(case, key), case.expected_facts[key], Limits(monthly=monthly, cash=cash), "N" if key == "current" else "S")


def test_clean_totals_annual_conversion_and_explicit_zero(cases):
    current, saved = check(cases["clean"]), check(cases["clean"], "saved")
    assert current["monthly_total"] == "3220.00"
    assert saved["monthly_total"] == "2900.00"
    assert saved["rows"]["mortgage_insurance"]["value"] == "0"
    assert current["cash_to_close"] == "94000"
    assert current["monthly_status"] == current["cash_status"] == "pass"
    assert "320.00 higher" in compare_deals(current, saved)[0]["message"]


def test_exact_limits_pass(cases):
    result = check(cases["clean"], monthly="3220", cash="94000")
    assert result["monthly_status"] == result["cash_status"] == "pass"


def test_missing_cost_does_not_become_zero_or_a_complete_total(cases):
    result = check(cases["missing"])
    assert result["monthly_total"] is None
    assert result["known_subtotal"] == "3070.00"
    assert result["monthly_status"] == "unknown"
    assert "home_insurance" in result["unresolved"]


def test_conflicting_cost_is_unknown(cases):
    result = check(cases["conflict"])
    assert result["rows"]["hoa"]["state"] == "conflicting"
    assert result["monthly_total"] is None
    assert result["rows"]["hoa"]["evidence_ids"] == ["N6", "N8"]


def test_known_subtotal_can_fail_even_when_other_cost_is_missing(cases):
    assert check(cases["missing"], monthly="3000")["monthly_status"] == "fail"
    assert check(cases["over_limit"])["monthly_status"] == "fail"


@pytest.mark.parametrize("change", [
    {"quote": "A made-up quote with USD 150 per month."},
    {"amount": "999"},
    {"period": "annual"},
    {"provenance": "reviewer"},
    {"field": "home_insurance"},
])
def test_rejects_unsupported_extraction(cases, change):
    fact = Fact.model_validate({**cases["clean"].expected_facts["current"][5].model_dump(), **change})
    accepted, errors = validate_facts(cases["clean"].current, [fact])
    assert not accepted and errors


def test_all_fixture_passages_are_grounded(cases):
    for case in cases.values():
        for key in ("current", "saved"):
            valid, errors = validate_facts(getattr(case, key), case.expected_facts[key])
            assert not errors
            assert len(valid) == len(case.expected_facts[key])


def test_reviewer_confirmation_replaces_conflict_and_preserves_provenance(cases):
    facts = apply_clarifications(cases["conflict"].expected_facts["current"], {"hoa": "175"}, ["hoa"])
    hoa = [f for f in facts if f.field == "hoa"]
    assert len(hoa) == 1 and hoa[0].provenance == "reviewer"
    result = summarize_deal(cases["conflict"].current, facts, Limits(monthly="3300", cash="95000"), "N")
    assert result["monthly_total"] == "3245.00"


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "100000001"])
def test_reviewer_cannot_supply_invalid_amounts(value):
    with pytest.raises(ValueError):
        apply_clarifications([], {"hoa": value}, ["hoa"])


def test_reviewer_cannot_change_an_unrequested_field():
    with pytest.raises(ValueError):
        apply_clarifications([], {"cash_to_close": "1"}, ["hoa"])


def test_blank_answer_keeps_missing_information(cases):
    facts = cases["missing"].expected_facts["current"]
    assert apply_clarifications(facts, {"home_insurance": None}, ["home_insurance"]) == facts


def test_coverage_audit_recovers_silently_omitted_conflicting_quote(cases):
    case = cases["conflict"]
    audited, warnings = audit_source_coverage(case.current, case.expected_facts["current"][:-1])
    assert len(audited) == 8
    assert warnings
    result = summarize_deal(case.current, audited, Limits(monthly="3300", cash="95000"), "N")
    assert result["rows"]["hoa"]["state"] == "conflicting"
    assert result["monthly_status"] == "unknown"


def test_coverage_audit_does_not_invent_missing_insurance(cases):
    case = cases["missing"]
    audited, warnings = audit_source_coverage(case.current, case.expected_facts["current"])
    assert not warnings
    assert all(f.field != "home_insurance" for f in audited)

