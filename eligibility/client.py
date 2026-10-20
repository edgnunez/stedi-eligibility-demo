"""Thin HTTP client for Stedi's real-time eligibility endpoint.

This module knows about HTTP and nothing about healthcare. It sends a request,
retries the failures that are worth retrying, and turns HTTP errors into
specific exceptions so the rest of the app can react to each one.

Important distinction: a payer rejection (an X12 AAA error like "member not
found") comes back as HTTP 200 with an `errors` array in the body. That's a
successful API call with a business-level "no". This module only raises for
HTTP-level failures; the parser and rejections modules handle AAA errors.
"""
from __future__ import annotations

import os
import time

import httpx

BASE_URL = "https://healthcare.us.stedi.com/2026-06-01"
ELIGIBILITY_PATH = "/eligibility-check"

# 429 = rate limited; 502/503/504 = gateway or service temporarily unavailable.
# These are safe to retry: the request never produced a payer response.
RETRYABLE_STATUS = {429, 502, 503, 504}


class StediError(Exception):
    """Base class for every error this client raises."""

    def __init__(self, message: str, status_code: int | None = None, body: object = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class AuthError(StediError):
    """401/403: the API key is missing, wrong, revoked, or the wrong mode."""


class RequestRejected(StediError):
    """4xx: Stedi rejected the request itself before it reached a payer."""


class TransientError(StediError):
    """Rate limits, outages, or timeouts that persisted after retrying."""


class EligibilityClient:
    """Sends eligibility checks. Use as a context manager to reuse one connection.

    Reusing one httpx.Client matters for batch runs: it keeps the TCP/TLS
    connection open instead of reconnecting for every check.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 70.0,  # payers can take up to ~1 minute to respond
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,  # lets tests fake the network
    ):
        self.api_key = api_key or os.getenv("STEDI_API_KEY")
        if not self.api_key:
            raise AuthError("STEDI_API_KEY is not set. Add it to your .env file.")
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self._http = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": self.api_key},
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> "EligibilityClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def check(self, payload: dict) -> dict:
        """Send one eligibility check and return the parsed JSON body."""
        attempt = 0
        while True:
            try:
                response = self._http.post(ELIGIBILITY_PATH, json=payload)
            except httpx.TimeoutException as exc:
                # Not retried automatically: we already waited ~70s, and in
                # production every check is a billed transaction.
                raise TransientError(
                    "Timed out waiting for the payer. Try again shortly."
                ) from exc
            except httpx.TransportError as exc:
                raise TransientError(f"Could not reach Stedi: {exc}") from exc

            if response.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                self._wait(attempt, response.headers.get("Retry-After"))
                attempt += 1
                continue
            return self._handle(response)

    def _wait(self, attempt: int, retry_after: str | None) -> None:
        """Honor Retry-After if the server sent one; otherwise back off exponentially."""
        try:
            delay = float(retry_after) if retry_after else None
        except ValueError:
            delay = None
        if delay is None:
            delay = self.backoff_seconds * (2**attempt)  # 1s, 2s, 4s...
        time.sleep(delay)

    @staticmethod
    def _handle(response: httpx.Response) -> dict:
        status = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = None

        if 200 <= status < 300:
            if not isinstance(body, dict):
                raise StediError("Stedi returned a non-JSON success response.", status)
            return body

        message = body.get("message") if isinstance(body, dict) else None
        message = message or response.reason_phrase

        if status in (401, 403):
            raise AuthError(
                f"Stedi rejected the API key ({status}: {message}). "
                "Check STEDI_API_KEY in .env and that it's a Test-mode key.",
                status,
                body,
            )
        if status in RETRYABLE_STATUS or status >= 500:
            raise TransientError(
                f"Stedi is unavailable or rate limiting ({status}: {message}).", status, body
            )
        raise RequestRejected(f"Stedi rejected the request ({status}): {message}", status, body)
