"""The retry loop and offline loading. A fake client and a fake clock make
the 2-minute retry window testable in milliseconds."""
import pytest

from eligibility.client import RequestRejected
from eligibility.workflow import load_fixture, run_check

ACTIVE = {"id": "ec_ok", "plans": []}
PAYER_DOWN = {"errors": [{"code": "42", "followupAction": "Resubmission Allowed"}]}
NOT_FOUND = {"errors": [{"code": "75", "followupAction": "Resubmission Not Allowed"}]}


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = 0

    def check(self, payload):
        self.calls += 1
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, Exception):
            raise response
        return response


class FakeTime:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


def run(client, retry_seconds=120.0):
    t = FakeTime()
    result = run_check(client, {}, retry_seconds=retry_seconds, sleep=t.sleep, clock=t.clock)
    return result, t


def test_success_on_first_try():
    result, t = run(FakeClient(ACTIVE))
    assert result.body == ACTIVE and result.attempts == 1 and t.sleeps == []


def test_payer_down_then_recovers():
    result, t = run(FakeClient(PAYER_DOWN, ACTIVE))
    assert result.body == ACTIVE and result.attempts == 2
    assert t.sleeps == [2.0]


def test_backs_off_and_gives_up_inside_the_window():
    result, t = run(FakeClient(PAYER_DOWN), retry_seconds=10)
    assert t.sleeps == [2.0, 4.0]  # the next 8s wait would pass the 10s budget
    assert result.attempts == 3
    assert result.body == PAYER_DOWN  # reported, not raised


def test_non_retryable_rejection_returns_immediately():
    client = FakeClient(NOT_FOUND)
    result, t = run(client)
    assert client.calls == 1 and t.sleeps == [] and result.body == NOT_FOUND


def test_400_with_aaa_errors_is_an_answer():
    body = {"errors": [{"code": "79", "followupAction": "Please Correct and Resubmit"}]}
    result, _ = run(FakeClient(RequestRejected("bad", 400, body)))
    assert result.http_status == 400 and result.body == body


def test_400_without_aaa_errors_still_raises():
    with pytest.raises(RequestRejected):
        run(FakeClient(RequestRejected("bad", 400, {"message": "payerId is required"})))


def test_load_fixture_handles_both_formats():
    bare = load_fixture("uhc-active")          # saved by smoke_test.py
    wrapped = load_fixture("aaa-79")           # saved by record_fixtures.py
    assert bare.http_status == 200 and bare.body["payerId"] == "87726"
    assert wrapped.http_status == 400 and wrapped.source == "fixture"


def test_missing_fixture_explains_how_to_record_it():
    with pytest.raises(FileNotFoundError, match="record_fixtures.py cigna-active"):
        load_fixture("cigna-active")
