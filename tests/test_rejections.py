"""Rejection explanations. Error shapes follow Stedi's documented `errors[]`
fields (code, description, followupAction, location, possibleResolutions).
Once record_fixtures.py has run, real responses get their own tests too."""
from eligibility.rejections import (
    DEVELOPER, ENROLLMENT, FRONT_DESK, STEDI, WAIT,
    errors_from_body, explain, should_retry,
)


def err(code, followup="Please Correct and Resubmit", **extra):
    return {"code": code, "description": f"Payer text for {code}",
            "followupAction": followup, "location": "SUBSCRIBER", **extra}


def test_member_id_problem_goes_to_front_desk():
    e = explain(err("72"))
    assert e.who_acts == FRONT_DESK
    assert "member ID" in e.meaning
    assert e.retryable is False


def test_not_found_and_bad_name_go_to_front_desk():
    assert explain(err("75")).who_acts == FRONT_DESK
    assert explain(err("73")).who_acts == FRONT_DESK


def test_provider_id_problem_is_an_enrollment_issue():
    assert explain(err("43")).who_acts == ENROLLMENT


def test_payer_down_is_retryable():
    e = explain(err("42", followup="Resubmission Allowed"))
    assert e.who_acts == WAIT and e.retryable is True
    assert explain(err("80", followup="Resubmission Allowed")).retryable is True


def test_42_with_correct_and_resubmit_is_not_retried():
    """Stedi: for 42 with follow-up C, N, or S the request itself is the problem."""
    e = explain(err("42", followup="Please Correct and Resubmit"))
    assert e.retryable is False
    assert e.who_acts == STEDI


def test_followup_letter_is_normalized():
    e = explain(err("42", followup="C"))
    assert e.followup_action == "Please Correct and Resubmit"
    assert e.retryable is False


def test_79_escalates_and_is_not_retried():
    e = explain(err("79", possibleResolutions="Payer XYZ is not configured."))
    assert e.who_acts == STEDI and e.retryable is False
    assert e.possible_resolutions == "Payer XYZ is not configured."


def test_request_content_errors_go_to_developer():
    assert explain(err("62")).who_acts == DEVELOPER


def test_unknown_code_falls_back_to_payer_text_and_escalates():
    e = explain(err("ZZ"))
    assert e.meaning == "Payer text for ZZ"
    assert e.who_acts == STEDI


def test_should_retry_requires_all_errors_retryable():
    assert should_retry([err("42", "Resubmission Allowed")]) is True
    assert should_retry([err("42", "Resubmission Allowed"), err("72")]) is False
    assert should_retry([]) is False


def test_errors_from_400_body():
    body = {"status": "ERROR", "errors": [err("79")]}
    assert errors_from_body(body)[0]["code"] == "79"
    assert errors_from_body(None) == []
    assert errors_from_body({"message": "Unauthorized"}) == []
