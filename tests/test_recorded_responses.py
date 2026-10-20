"""Tests against real Stedi test-mode responses saved by record_fixtures.py.

Each fixture is {"http_status": ..., "body": ...}. All data is Stedi mock data.
"""
import json
from pathlib import Path

import pytest

from eligibility.parser import parse_eligibility
from eligibility.rejections import (
    ENROLLMENT, FRONT_DESK, STEDI, WAIT, errors_from_body, explain, should_retry,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def only_error(name):
    [error] = errors_from_body(load(name)["body"])
    return explain(error)


def test_inactive_coverage():
    record = load("uhc_inactive")
    assert record["http_status"] == 200
    summary = parse_eligibility(record["body"])
    assert summary.coverage_status == "INACTIVE"
    assert summary.is_active is False
    assert summary.cost_shares == []
    assert summary.rejections == []


@pytest.mark.parametrize("name", ["aaa_42", "aaa_43", "aaa_72", "aaa_73", "aaa_75"])
def test_payer_rejections_arrive_as_http_200(name):
    """A payer saying no is still a successful API call."""
    record = load(name)
    assert record["http_status"] == 200
    summary = parse_eligibility(record["body"])
    assert summary.is_active is None  # no coverage info when the payer rejects
    assert len(summary.rejections) == 1


def test_42_payer_unavailable_is_retried():
    e = only_error("aaa_42")
    assert e.followup_action == "Resubmission Allowed"
    assert e.who_acts == WAIT and e.retryable is True
    assert should_retry(load("aaa_42")["body"]["errors"]) is True


def test_43_is_a_provider_enrollment_issue():
    e = only_error("aaa_43")
    assert e.location == "PROVIDER"
    assert e.who_acts == ENROLLMENT and e.retryable is False


@pytest.mark.parametrize("name", ["aaa_72", "aaa_73", "aaa_75"])
def test_patient_data_problems_go_to_front_desk(name):
    e = only_error(name)
    assert e.location == "SUBSCRIBER"
    assert e.who_acts == FRONT_DESK and e.retryable is False
    assert e.possible_resolutions  # Stedi's own guidance is passed through


def test_75_says_resubmission_not_allowed():
    """Retrying the same data won't help; the data has to change first."""
    assert only_error("aaa_75").followup_action == "Resubmission Not Allowed"


@pytest.mark.parametrize("name,location", [("aaa_79", "PAYER"), ("unknown_payer", "ENVELOPE")])
def test_79_comes_back_as_http_400_with_aaa_inside(name, location):
    record = load(name)
    assert record["http_status"] == 400
    e = only_error(name)
    assert e.code == "79" and e.location == location
    assert e.who_acts == STEDI and e.retryable is False
