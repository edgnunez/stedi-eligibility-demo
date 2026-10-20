"""Parser tests against a real Stedi test-mode response (mock data, no PHI)."""
import json
from pathlib import Path

import pytest

from eligibility.parser import parse_eligibility

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def uhc_active():
    body = json.loads((FIXTURES / "uhc_active.json").read_text())
    return parse_eligibility(body)


def test_coverage_is_active(uhc_active):
    assert uhc_active.coverage_status == "ACTIVE_COVERAGE"
    assert uhc_active.is_active is True
    assert uhc_active.rejections == []


def test_plan_details(uhc_active):
    # plans[].name is empty in this response, so this exercises the fallback.
    assert uhc_active.plan_name == "Gold Plan HMO"
    assert uhc_active.insurance_type == "COMMERCIAL"
    assert uhc_active.plan_start == "2024-01-01"
    assert uhc_active.group_number == "111222"
    assert uhc_active.payer_name == "UNITEDHEALTHCARE"
    assert uhc_active.patient_name == "JANE D DOE"
    assert uhc_active.member_id == "UHC123456"


def test_deductible_remaining(uhc_active):
    [ind] = uhc_active.find("deductible", "30", "INDIVIDUAL", "REMAINING")
    assert ind.amount == "0"
    assert ind.network == "IN_NETWORK"


def test_out_of_pocket(uhc_active):
    def oop(level, period):
        [entry] = uhc_active.find("out_of_pocket", "30", level, period)
        return entry.amount

    assert oop("INDIVIDUAL", "CALENDAR_YEAR") == "1500"
    assert oop("INDIVIDUAL", "YEAR_TO_DATE") == "650"
    assert oop("INDIVIDUAL", "REMAINING") == "850"
    # Family figures don't add up (925 + 2200 != 3000). We report them as-is.
    assert oop("FAMILY", "CALENDAR_YEAR") == "3000"
    assert oop("FAMILY", "REMAINING") == "2200"


def test_copays(uhc_active):
    [urgent] = uhc_active.find("copay", "UC")
    assert urgent.amount == "15"
    [er] = uhc_active.find("copay", "86")
    assert er.amount == "50"
    assert er.messages == ["COPAY WAIVED IF ADMITTED"]


def test_conflicting_copays_keep_their_messages(uhc_active):
    """Two well-visit copays: $0 for a periodic health evaluation, $15 otherwise."""
    well_visits = uhc_active.find("copay", "BZ")
    by_amount = {c.amount: c.messages for c in well_visits}
    assert by_amount == {"0": ["OUTPATIENT PERIODIC HEALTH EVALUATION"], "15": []}


def test_coinsurance_is_percent(uhc_active):
    coins = uhc_active.find("coinsurance")
    assert coins and all(c.percent == "0" and c.amount is None for c in coins)


def test_empty_response_does_not_crash():
    summary = parse_eligibility({"id": "ec_test"})
    assert summary.is_active is None
    assert summary.plan_name is None
    assert summary.cost_shares == []


def test_rejection_passes_through():
    body = {"errors": [{"code": "72", "description": "Invalid/Missing Subscriber/Insured ID",
                        "followupAction": "Please Correct and Resubmit", "location": "SUBSCRIBER"}]}
    summary = parse_eligibility(body)
    assert summary.is_active is None
    assert summary.rejections[0]["code"] == "72"
