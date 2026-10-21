"""What a person actually sees. Rendered from the real saved responses."""
from eligibility.display import money, percent, render
from eligibility.workflow import load_fixture


def show(name, raw=False):
    return render(load_fixture(name), raw=raw)


def test_money_and_percent_formatting():
    assert money("1500") == "$1,500"
    assert money("12.5") == "$12.50"
    assert money("not-a-number") == "not-a-number"  # shown as sent, never hidden
    assert percent("0.2") == "20%"   # X12 sends coinsurance as a fraction
    assert percent("0") == "0%"


def test_active_summary():
    text = show("uhc-active")
    assert "COVERAGE: ACTIVE" in text
    assert "Gold Plan HMO" in text
    assert "$1,500" in text and "$850" in text
    assert "COPAY WAIVED IF ADMITTED" in text
    assert "PCP TO SUBMIT A SPECIALIST REFERRAL" in text


def test_inconsistent_family_figures_are_flagged_not_fixed():
    text = show("uhc-active")
    assert "don't add up" in text and "$3,000" in text


def test_inactive_summary():
    text = show("uhc-inactive")
    assert "COVERAGE: INACTIVE" in text and "insurance discovery" in text


def test_rejection_view():
    text = show("aaa-75")
    assert "AAA 75" in text
    assert "Front desk" in text
    assert "Resubmission Not Allowed" in text
    assert "Stedi's guidance" in text


def test_400_rejection_view_explains_the_status():
    text = show("unknown-payer")
    assert "HTTP 400" in text and "AAA 79" in text and "Stedi support" in text


def test_raw_view_annotates_x12():
    active = show("uhc-active", raw=True)
    assert "EB*B*IND*86***27*50" in active
    assert "Co-Payment | individual | emergency | visit | $50 | in network" in active
    rejected = show("aaa-75", raw=True)
    assert "AAA*N**75*N" in rejected
    assert "follow-up N = Resubmission Not Allowed" in rejected
