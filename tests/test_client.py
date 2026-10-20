"""Client tests with a fake network (httpx.MockTransport). No real API calls."""
import httpx
import pytest

from eligibility.client import AuthError, EligibilityClient, RequestRejected, TransientError


def make_client(handler, **kwargs):
    return EligibilityClient(
        api_key="test-key",
        transport=httpx.MockTransport(handler),
        backoff_seconds=0,  # don't actually sleep in tests
        **kwargs,
    )


def test_sends_key_in_authorization_header():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"id": "ec_1"})

    with make_client(handler) as client:
        assert client.check({}) == {"id": "ec_1"}
    assert seen["auth"] == "test-key"


def test_401_raises_auth_error():
    handler = lambda request: httpx.Response(401, json={"message": "Unauthorized"})
    with make_client(handler) as client, pytest.raises(AuthError) as exc:
        client.check({})
    assert exc.value.status_code == 401


def test_400_raises_request_rejected_with_message():
    handler = lambda request: httpx.Response(400, json={"message": "payerId is required"})
    with make_client(handler) as client, pytest.raises(RequestRejected, match="payerId is required"):
        client.check({})


def test_429_is_retried_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, json={"message": "Too Many Requests"})
        return httpx.Response(200, json={"id": "ec_2"})

    with make_client(handler) as client:
        assert client.check({})["id"] == "ec_2"
    assert len(calls) == 2


def test_persistent_503_gives_up_after_retries():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, json={"message": "Service Unavailable"})

    with make_client(handler, max_retries=2) as client, pytest.raises(TransientError):
        client.check({})
    assert len(calls) == 3  # first try + 2 retries


def test_payer_rejection_is_not_an_http_error():
    """AAA errors arrive as HTTP 200. The client returns them; it doesn't raise."""
    body = {"errors": [{"code": "75", "description": "Subscriber/Insured Not Found"}]}
    with make_client(lambda request: httpx.Response(200, json=body)) as client:
        assert client.check({})["errors"][0]["code"] == "75"


def test_missing_key_fails_fast(monkeypatch):
    monkeypatch.delenv("STEDI_API_KEY", raising=False)
    with pytest.raises(AuthError, match="not set"):
        EligibilityClient()
