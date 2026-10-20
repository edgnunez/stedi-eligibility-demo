"""Run the inactive, rejection, and unknown-payer mock scenarios and save each
response to fixtures/ so tests and offline demos can use real Stedi output.

Test mode only: every request is a predefined Stedi mock (no real patients).
Usage:  python record_fixtures.py            (all scenarios below)
        python record_fixtures.py aaa-75     (just one)
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from eligibility.client import EligibilityClient, StediError
from eligibility.rejections import errors_from_body
from eligibility.scenarios import SCENARIOS

TO_RECORD = ["uhc-inactive", "aaa-42", "aaa-43", "aaa-72", "aaa-73",
             "aaa-75", "aaa-79", "unknown-payer"]
FIXTURES = Path("fixtures")


def main() -> None:
    load_dotenv()
    names = sys.argv[1:] or TO_RECORD
    FIXTURES.mkdir(exist_ok=True)

    with EligibilityClient() as client:
        for name in names:
            scenario = SCENARIOS[name]
            try:
                body, status = client.check(scenario.request), 200
            except StediError as exc:
                # Keep error bodies too: an unknown payer is documented as HTTP 400.
                body, status = exc.body, exc.status_code
                print(f"{name:14} HTTP {status}: {exc}")

            record = {"http_status": status, "body": body}
            out = FIXTURES / f"{name.replace('-', '_')}.json"
            out.write_text(json.dumps(record, indent=2))

            codes = [e.get("code") for e in errors_from_body(body)]
            print(f"{name:14} -> {out}  HTTP {status}  AAA {codes or '-'}  "
                  f"coverage {first_status(body) or '-'}")


def first_status(body: object) -> str | None:
    """The first coverage status in the response, e.g. ACTIVE_COVERAGE."""
    if not isinstance(body, dict):
        return None
    for plan in body.get("plans") or []:
        for status in (plan.get("benefits") or {}).get("statuses") or []:
            return status.get("status")
    return None


if __name__ == "__main__":
    main()
