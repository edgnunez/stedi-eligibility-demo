"""Explain payer rejections (X12 AAA errors) in plain English.

When a payer can't answer an eligibility check, the 271 response carries an
AAA segment instead of benefits. Stedi passes each one through in `errors[]`
with a code, the payer's description, a follow-up action, and a location.

The useful question on a support call isn't "what does code 72 mean" but
"who has to fix this, and should we try again?" So every code maps to:
  - who acts: the front desk, the provider's enrollment team, the
    integration developer, nobody (wait and retry), or Stedi support
  - the next step
  - whether an automatic retry makes sense

Retry rules follow Stedi's eligibility troubleshooting guide:
https://www.stedi.com/docs/healthcare/eligibility-troubleshooting
"""
from __future__ import annotations

from dataclasses import dataclass

# --- Who acts, and what they do ------------------------------------------------

FRONT_DESK = "Front desk / patient intake"
ENROLLMENT = "Provider's billing or credentialing team"
DEVELOPER = "Integration developer"
WAIT = "No one: payer-side, temporary"
STEDI = "Stedi support"

CATEGORIES = {
    FRONT_DESK: "Check the patient's insurance card and re-enter member ID, name "
                "(exactly as printed) and date of birth. Sending all four fields "
                "gives the payer the best chance of a match.",
    ENROLLMENT: "Confirm the provider's NPI is registered with this payer and that "
                "any required enrollment or agreement is complete. This is a "
                "setup issue, not a data-entry or code issue.",
    DEVELOPER: "Fix the request itself: dates, service codes, or required fields "
               "the payer expects.",
    WAIT: "Retry automatically. For real-time checks, Stedi recommends retrying "
          "right away for up to about 2 minutes, then failing gracefully.",
    STEDI: "Double-check the payer ID first. If it's correct, escalate to Stedi "
           "support: there's a connection problem between Stedi and this payer.",
}

# AAA reject reason codes (X12 005010X279A1) -> who acts.
CODE_OWNERS = {
    # Patient and subscriber data: almost always fixable at intake
    "58": FRONT_DESK, "60": FRONT_DESK, "64": FRONT_DESK, "65": FRONT_DESK,
    "66": FRONT_DESK, "67": FRONT_DESK, "68": FRONT_DESK, "71": FRONT_DESK,
    "72": FRONT_DESK, "73": FRONT_DESK, "74": FRONT_DESK, "75": FRONT_DESK,
    "76": FRONT_DESK, "77": FRONT_DESK, "78": FRONT_DESK,
    # Provider identity and enrollment
    "41": ENROLLMENT, "43": ENROLLMENT, "44": ENROLLMENT, "45": ENROLLMENT,
    "46": ENROLLMENT, "47": ENROLLMENT, "48": ENROLLMENT, "49": ENROLLMENT,
    "50": ENROLLMENT, "51": ENROLLMENT, "52": ENROLLMENT, "97": ENROLLMENT,
    # Request content
    "04": DEVELOPER, "15": DEVELOPER, "53": DEVELOPER, "54": DEVELOPER,
    "55": DEVELOPER, "56": DEVELOPER, "57": DEVELOPER, "61": DEVELOPER,
    "62": DEVELOPER, "63": DEVELOPER, "69": DEVELOPER, "70": DEVELOPER,
    "98": DEVELOPER,
    # Payer temporarily unavailable
    "42": WAIT, "80": WAIT,
    # Payer connectivity or configuration
    "79": STEDI, "T4": STEDI,
}

# Plain-English meanings for the codes a demo or support call hits most.
MEANINGS = {
    "41": "The payer restricted access for this provider, usually an enrollment "
          "problem or bad payer-portal credentials.",
    "42": "The payer's system couldn't answer right now: an outage, maintenance, "
          "or the payer throttling requests.",
    "43": "The payer doesn't recognize the provider's NPI, or it's not registered "
          "correctly with them.",
    "58": "The date of birth is missing or doesn't match the payer's records.",
    "71": "The date of birth doesn't match the patient the payer found.",
    "72": "The member ID is wrong, missing, or not in the format this payer expects.",
    "73": "The subscriber's name is missing, misspelled, or doesn't match the payer's records.",
    "75": "The payer searched and couldn't find this person at all.",
    "79": "Stedi couldn't connect to this payer, often because the payer ID is "
          "wrong or the payer isn't configured.",
    "80": "The payer never responded, so the transaction timed out.",
    "T4": "The payer name or ID is missing or unrecognized.",
}

# X12 follow-up action codes (AAA04). The JSON may carry the letter or the text.
FOLLOWUP_TEXT = {
    "C": "Please Correct and Resubmit",
    "N": "Resubmission Not Allowed",
    "P": "Please Resubmit Original Transaction",
    "R": "Resubmission Allowed",
    "S": "Do Not Resubmit; Inquiry Initiated to a Third Party",
    "W": "Please Wait 30 Days and Resubmit",
    "X": "Please Wait 10 Days and Resubmit",
    "Y": "Do Not Resubmit; We Will Hold Your Request and Respond Again Shortly",
}
# For code 42, these follow-ups mean "your request is the problem": don't retry.
NO_RETRY_FOLLOWUPS = {FOLLOWUP_TEXT["C"], FOLLOWUP_TEXT["N"], FOLLOWUP_TEXT["S"]}

RETRYABLE_CODES = {"42", "80"}


@dataclass
class Explanation:
    code: str
    payer_description: str     # the payer's own wording
    meaning: str               # our plain-English version
    who_acts: str
    next_step: str
    retryable: bool
    followup_action: str | None
    location: str | None       # which loop of the 271: SUBSCRIBER, PROVIDER, ...
    possible_resolutions: str | None  # Stedi's own guidance, when present


def normalize_followup(value: str | None) -> str | None:
    """Turn a follow-up action letter ("C") into its text; leave text unchanged."""
    if not value:
        return None
    return FOLLOWUP_TEXT.get(value.strip().upper(), value.strip())


def is_retryable(code: str, followup_action: str | None) -> bool:
    if code not in RETRYABLE_CODES:
        return False
    return normalize_followup(followup_action) not in NO_RETRY_FOLLOWUPS


def explain(error: dict) -> Explanation:
    code = str(error.get("code", "")).strip().upper()
    description = error.get("description") or "No description provided"
    followup = normalize_followup(error.get("followupAction"))
    retryable = is_retryable(code, followup)

    who = CODE_OWNERS.get(code, STEDI)  # unknown code: safest to escalate
    next_step = CATEGORIES[who]
    if code == "42" and not retryable:
        # Same code, different meaning: the payer is telling us the request is bad.
        who, next_step = STEDI, (
            "The payer says retrying won't help. Review the request, then "
            "contact Stedi support if it looks correct."
        )

    return Explanation(
        code=code,
        payer_description=description,
        meaning=MEANINGS.get(code, description),
        who_acts=who,
        next_step=next_step,
        retryable=retryable,
        followup_action=followup,
        location=error.get("location"),
        possible_resolutions=error.get("possibleResolutions"),
    )


def explain_all(errors: list[dict]) -> list[Explanation]:
    return [explain(e) for e in errors or []]


def should_retry(errors: list[dict]) -> bool:
    """Retry only if there are errors and every one of them is retryable."""
    explanations = explain_all(errors)
    return bool(explanations) and all(e.retryable for e in explanations)


def errors_from_body(body: object) -> list[dict]:
    """Pull AAA errors out of any response body, including a 400 error body.

    Stedi documents that an unknown payer ID returns HTTP 400 whose body still
    contains an AAA 79 error, so the CLI checks error bodies too.
    """
    if isinstance(body, dict) and isinstance(body.get("errors"), list):
        return [e for e in body["errors"] if isinstance(e, dict)]
    return []
