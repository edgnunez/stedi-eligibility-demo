"""Turn Stedi's eligibility response (the JSON form of an X12 271) into a summary.

Design choices worth knowing:
- We report what the payer said and never do arithmetic on it. Payer data
  isn't always internally consistent (see the README), so a summary that
  computes "remaining = max - used" can contradict the payer's own number.
- We keep every cost-share entry with its free-text messages. Payers often
  send two copays for the same service that differ only by a message
  (for example $15 specialist vs $0 "OUTPATIENT MATERNITY"), so dropping
  messages would turn correct data into misleading data.
- Every field is optional. Real payer responses omit things, so the parser
  degrades to None instead of crashing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Stedi benefit array name -> our short name.
COST_SHARE_TYPES = {
    "coPayment": "copay",
    "coInsurance": "coinsurance",
    "deductible": "deductible",
    "outOfPocket": "out_of_pocket",
}

PLAN_COVERAGE_STC = "30"  # Health Benefit Plan Coverage: the overall plan status


@dataclass
class CostShare:
    kind: str                    # copay | coinsurance | deductible | out_of_pocket
    service_code: str | None     # STC, e.g. "UC"
    service_name: str | None     # e.g. "Urgent Care"
    coverage_level: str | None   # INDIVIDUAL, FAMILY, ...
    network: str | None          # IN_NETWORK, OUT_OF_NETWORK, IN_AND_OUT_OF_NETWORK
    time_period: str | None      # VISIT, CALENDAR_YEAR, YEAR_TO_DATE, REMAINING, ...
    amount: str | None           # dollars (copay, deductible, out-of-pocket)
    percent: str | None          # coinsurance
    messages: list[str] = field(default_factory=list)


@dataclass
class EligibilitySummary:
    check_id: str | None
    payer_name: str | None
    patient_name: str | None
    member_id: str | None
    coverage_status: str | None  # raw value from the payer, e.g. ACTIVE_COVERAGE
    is_active: bool | None       # None means we couldn't tell
    plan_name: str | None
    insurance_type: str | None
    plan_start: str | None
    group_number: str | None
    plan_count: int
    cost_shares: list[CostShare]
    rejections: list[dict]       # raw AAA errors; explained in rejections.py
    invalid_entry_count: int     # entries Stedi flagged as not matching their type

    def find(
        self,
        kind: str,
        service_code: str | None = None,
        coverage_level: str | None = None,
        time_period: str | None = None,
    ) -> list[CostShare]:
        """Filter cost shares, e.g. find("out_of_pocket", "30", "INDIVIDUAL", "REMAINING")."""
        return [
            c for c in self.cost_shares
            if c.kind == kind
            and (service_code is None or c.service_code == service_code)
            and (coverage_level is None or c.coverage_level == coverage_level)
            and (time_period is None or c.time_period == time_period)
        ]


def parse_eligibility(body: dict) -> EligibilitySummary:
    plans = body.get("plans") or []
    plan = plans[0] if plans else {}
    benefits = plan.get("benefits") or {}

    # A dependent check returns the patient in `dependent` (for most payers).
    patient = body.get("dependent") or body.get("subscriber") or {}
    subscriber = body.get("subscriber") or {}

    status_entry = _plan_status_entry(benefits.get("statuses") or [])
    coverage_status = status_entry.get("status")

    return EligibilitySummary(
        check_id=body.get("id"),
        payer_name=_name((body.get("payer") or {}).get("name")),
        patient_name=_name(patient.get("name")),
        member_id=subscriber.get("memberId"),
        coverage_status=coverage_status,
        is_active=_is_active(coverage_status),
        plan_name=_plan_name(plan, benefits, status_entry),
        insurance_type=status_entry.get("insuranceType"),
        plan_start=_dig(patient, "dates", "plan", "start"),
        group_number=_dig(subscriber, "additionalInformation", "group", "number"),
        plan_count=len(plans),
        cost_shares=_cost_shares(benefits),
        rejections=list(body.get("errors") or []),
        invalid_entry_count=sum(
            len(v) for v in (benefits.get("invalidEntries") or {}).values()
        ),
    )


def _plan_status_entry(statuses: list[dict]) -> dict:
    """Prefer the STC 30 entry: it describes the plan as a whole, not one service."""
    for entry in statuses:
        if _dig(entry, "service", "value") == PLAN_COVERAGE_STC:
            return entry
    return statuses[0] if statuses else {}


def _is_active(status: str | None) -> bool | None:
    # X12 EB01 codes 1-5 are "active" variants and 6-8 are "inactive" variants.
    # We confirm the exact inactive string against the uhc-inactive mock in step 4.
    if not status:
        return None
    if status.startswith("INACTIVE"):
        return False
    if status.startswith("ACTIVE"):
        return True
    return None


def _plan_name(plan: dict, benefits: dict, status_entry: dict) -> str | None:
    """Use plans[].name when present; otherwise fall back to planCoverageDescription.

    The UHC mock leaves plans[].name empty and puts "Gold Plan HMO" in the
    status entry's planCoverageDescription (X12 EB05), so the fallback matters.
    """
    if plan.get("name"):
        return plan["name"]
    if status_entry.get("planCoverageDescription"):
        return status_entry["planCoverageDescription"]
    for entries in benefits.values():
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and entry.get("planCoverageDescription"):
                    return entry["planCoverageDescription"]
    return None


def _cost_shares(benefits: dict) -> list[CostShare]:
    shares = []
    for key, kind in COST_SHARE_TYPES.items():
        for entry in benefits.get(key) or []:
            service = entry.get("service") or {}
            shares.append(CostShare(
                kind=kind,
                service_code=service.get("value"),
                service_name=service.get("definition"),
                coverage_level=entry.get("coverageLevel"),
                network=_dig(entry, "network", "indicator"),
                time_period=entry.get("timePeriod"),
                amount=entry.get("amount"),
                percent=entry.get("percent"),
                messages=list(entry.get("messages") or []),
            ))
    return shares


def _name(name: dict | None) -> str | None:
    if not name:
        return None
    if name.get("organization"):
        return name["organization"]
    person = name.get("person") or {}
    parts = [person.get(k) for k in ("firstName", "middleName", "lastName", "suffix")]
    return " ".join(p for p in parts if p) or None


def _dig(data: dict, *keys: str):
    """Safely walk nested dicts: _dig(x, "a", "b") is x["a"]["b"] or None."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data
