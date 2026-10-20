"""Step 1 smoke test: send one mock eligibility check to Stedi and save the response.

Uses Stedi's test mode, so all data is predefined mock data (no real patients).
"""
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

URL = "https://healthcare.us.stedi.com/2026-06-01/eligibility-check"

# Stedi's "UHC - Mock request 1" (active coverage). Subscriber values must
# match Stedi's mock list exactly, or test mode returns an error.
REQUEST = {
    "payerId": "87726",  # UnitedHealthcare
    "provider": {
        "name": {"organization": "Provider Name"},
        "npi": "1999999984",  # dummy NPI that passes check-digit validation
    },
    "subscriber": {
        "name": {"person": {"firstName": "Jane", "lastName": "Doe"}},
        "memberId": "UHC123456",
        "dateOfBirth": "1971-01-01",
    },
    "encounter": {
        "services": [{"system": "STC", "value": "30"}]  # 30 = health plan coverage
    },
}


def main() -> None:
    load_dotenv()  # reads .env into environment variables
    api_key = os.getenv("STEDI_API_KEY")
    if not api_key:
        sys.exit("STEDI_API_KEY is missing. Add it to your .env file.")

    # Payers can take up to ~1 minute to respond, so allow a bit more than that.
    response = httpx.post(
        URL,
        headers={"Authorization": api_key},
        json=REQUEST,  # httpx serializes this and sets Content-Type: application/json
        timeout=70.0,
    )
    print(f"HTTP {response.status_code}")

    try:
        body = response.json()
    except ValueError:
        print("Response was not JSON:")
        print(response.text[:500])
        sys.exit(1)

    out = Path("fixtures/uhc_active.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(body, indent=2))
    print(f"Saved full response to {out}\n")

    print("Top-level keys:", sorted(body))
    for plan in body.get("plans", []):
        benefits = plan.get("benefits", {})
        print(f"\nPlan: {plan.get('name')}")
        print("  Benefit types:", sorted(benefits))
        statuses = benefits.get("statuses", [])
        if statuses:
            print("  First status entry:")
            print(json.dumps(statuses[0], indent=2))
    if body.get("errors"):
        print("\nErrors:", json.dumps(body["errors"], indent=2))


if __name__ == "__main__":
    main()
