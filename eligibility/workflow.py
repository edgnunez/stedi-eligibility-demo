"""Run one eligibility check end to end: send it, retry if the payer is
temporarily down, and return the payer's answer. Also loads saved fixtures so
the demo can run offline.

Retry policy (from Stedi's troubleshooting guide): for real-time checks,
retry AAA 42/80 for up to about 2 minutes, then fail gracefully. We back off
2s, 4s, 8s... inside that window rather than retrying in a tight loop, which
would send dozens of identical requests to a payer that's already struggling.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from eligibility.client import RequestRejected
from eligibility.rejections import errors_from_body, should_retry


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@dataclass
class CheckResult:
    http_status: int
    body: dict
    attempts: int = 1
    source: str = "live"  # "live" or "fixture"
    checked_at: datetime | None = None    # when the payer answered (UTC)
    elapsed_seconds: float | None = None  # total time including retries


def run_check(
    client,
    payload: dict,
    *,
    retry_seconds: float = 120.0,
    first_delay: float = 2.0,
    max_delay: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    on_retry: Callable[[int, float, list], None] | None = None,
) -> CheckResult:
    start = clock()
    delay = first_delay
    attempt = 0
    
    def finish() -> CheckResult:
        return CheckResult(status, body, attempt,
                           checked_at=now(),
                           elapsed_seconds=round(clock() - start, 3))
    while True:
        attempt += 1
        try:
            body, status = client.check(payload), 200
        except RequestRejected as exc:
            # A 400 can still carry the payer's AAA errors (e.g. unknown payer
            # ID -> AAA 79). That's an answer to show, not a crash.
            if not errors_from_body(exc.body):
                raise
            body, status = exc.body, exc.status_code

        errors = errors_from_body(body)
        if not should_retry(errors):
            return finish()
        if clock() - start + delay > retry_seconds:
            return finish()  # out of time: report it
        if on_retry:
            on_retry(attempt, delay, errors)
        sleep(delay)
        delay = min(delay * 2, max_delay)


def fixture_path(scenario_name: str) -> Path:
    return FIXTURES / f"{scenario_name.replace('-', '_')}.json"


def load_fixture(scenario_name: str) -> CheckResult:
    """Load a saved response. Supports both formats in fixtures/:
    smoke_test.py saved a bare body; record_fixtures.py saves
    {"http_status": ..., "body": ...}."""
    path = fixture_path(scenario_name)
    if not path.exists():
        raise FileNotFoundError(
            f"No saved response for '{scenario_name}'. Run it online, or record "
            f"it with: python record_fixtures.py {scenario_name}"
        )
    data = json.loads(path.read_text())
    if isinstance(data, dict) and set(data) == {"http_status", "body"}:
        return CheckResult(data["http_status"], data["body"] or {}, source="fixture")
    return CheckResult(200, data, source="fixture")