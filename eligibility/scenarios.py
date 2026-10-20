"""Named mock eligibility requests from Stedi's test-mode documentation.

Test mode only accepts these exact subscriber values. Any other name, member
ID, or date of birth returns an error. All of this is fictional mock data.
Source: https://www.stedi.com/docs/healthcare/api-reference/mock-requests-eligibility-checks
"""
from __future__ import annotations

from dataclasses import dataclass

# Any organization name works in test mode; the NPI must pass check-digit validation.
PROVIDER = {"name": {"organization": "Demo Clinic"}, "npi": "1999999984"}


def build_request(
    payer_id: str,
    first_name: str,
    last_name: str,
    member_id: str,
    date_of_birth: str,  # YYYY-MM-DD on the 2026-06-01 endpoint
    service_type_code: str = "30",  # 30 = Health Benefit Plan Coverage
    provider_name: str | None = None,
) -> dict:
    """Build the JSON body that Stedi translates into an X12 270."""
    provider = PROVIDER
    if provider_name:
        provider = {**PROVIDER, "name": {"organization": provider_name}}
    return {
        "payerId": payer_id,
        "provider": provider,
        "subscriber": {
            "name": {"person": {"firstName": first_name, "lastName": last_name}},
            "memberId": member_id,
            "dateOfBirth": date_of_birth,
        },
        "encounter": {"services": [{"system": "STC", "value": service_type_code}]},
    }


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    request: dict


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in [
        Scenario("uhc-active", "UnitedHealthcare, active coverage",
                 build_request("87726", "Jane", "Doe", "UHC123456", "1971-01-01")),
        Scenario("uhc-inactive", "UnitedHealthcare, inactive coverage",
                 build_request("87726", "Jane", "Doe", "UHCINACTIVE", "1971-01-01")),
        Scenario("cigna-active", "Cigna, active coverage",
                 build_request("62308", "James", "Jones", "23456789100", "1991-02-02")),
        Scenario("aetna-active", "Aetna, active coverage",
                 build_request("60054", "Jane", "Doe", "AETNA12345", "2004-04-04")),
        Scenario("humana-active", "Humana, active coverage",
                 build_request("61101", "Jane", "Doe", "HUMANA123", "1975-05-05")),
        # Rejection mocks use "Medical Provider", matching Stedi's examples exactly.
        Scenario("aaa-42", "Payer rejection 42: payer unable to respond",
                 build_request("87726", "Jane", "Doe", "UHCAAA42", "2001-01-01",
                               provider_name="Medical Provider")),
        Scenario("aaa-43", "Payer rejection 43: invalid provider ID",
                 build_request("87726", "Jane", "Doe", "UHCAAA43", "1970-01-01",
                               provider_name="Medical Provider")),
        Scenario("aaa-72", "Payer rejection 72: invalid member ID",
                 build_request("87726", "John", "Doe", "UHCAAA72", "1990-01-01",
                               provider_name="Medical Provider")),
        Scenario("aaa-73", "Payer rejection 73: invalid subscriber name",
                 build_request("87726", "John", "Doe", "UHCAAA73", "1990-01-01",
                               provider_name="Medical Provider")),
        Scenario("aaa-75", "Payer rejection 75: subscriber not found",
                 build_request("87726", "Jane", "Doe", "UHCAAA75", "1990-01-01",
                               provider_name="Medical Provider")),
        Scenario("aaa-79", "Payer rejection 79: payer connection problem",
                 build_request("87726", "John", "Doe", "UHCAAA79", "1970-01-01",
                               provider_name="Medical Provider")),
        # Not a Stedi mock: a payer ID that doesn't exist. We record whatever
        # Stedi actually returns rather than assuming the documented shape.
        Scenario("unknown-payer", "Payer ID that doesn't exist",
                 build_request("NOTAPAYER", "Jane", "Doe", "UHC123456", "1971-01-01")),
    ]
}
