"""Turn a check result into text a front-desk person could read.

Three views:
  - coverage summary (active / inactive): plan, plan-level costs, visit costs
  - rejection view: what the AAA error means, who acts, retry or not
  - raw view (--raw): the X12 271 segments behind the summary, annotated

Plain text only, no extra dependencies.
"""
from __future__ import annotations

import re
import textwrap
from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from eligibility.parser import EligibilitySummary, parse_eligibility
from eligibility.rejections import FOLLOWUP_TEXT, Explanation, explain_all, errors_from_body
from eligibility.workflow import CheckResult

WIDTH = 88
RULE = "-" * WIDTH

# Services a clinic asks about most, shown first in the visit-cost table.
SERVICE_ORDER = ["BY", "BZ", "96", "UC", "86", "48", "50", "MH", "PT", "33", "AL", "35"]

PLAN_LEVEL_LABELS = {
    ("deductible", "CALENDAR_YEAR"): "Deductible (annual)",
    ("deductible", "YEAR_TO_DATE"): "Deductible met so far",
    ("deductible", "REMAINING"): "Deductible remaining",
    ("out_of_pocket", "CALENDAR_YEAR"): "Out-of-pocket max (annual)",
    ("out_of_pocket", "YEAR_TO_DATE"): "Out-of-pocket spent so far",
    ("out_of_pocket", "REMAINING"): "Out-of-pocket remaining",
}
PERIOD_ORDER = ["CALENDAR_YEAR", "YEAR_TO_DATE", "REMAINING"]
NETWORK_LABELS = {
    "IN_NETWORK": "in network",
    "OUT_OF_NETWORK": "out of network",
    "IN_AND_OUT_OF_NETWORK": "in and out of network",
    None: "network not specified",
}


# --- Formatting helpers ----------------------------------------------------------

def money(amount: str | None) -> str:
    if amount is None:
        return "-"
    try:
        value = Decimal(amount)
    except InvalidOperation:
        return amount  # show payer data as-is rather than hide it
    return f"${value:,.0f}" if value == value.to_integral() else f"${value:,.2f}"


def percent(value: str | None) -> str:
    """X12 EB08 is a decimal fraction: 0.2 means 20%."""
    if value is None:
        return "-"
    try:
        pct = Decimal(value) * 100
    except InvalidOperation:
        return value
    return f"{pct:.0f}%" if pct == pct.to_integral() else f"{pct.normalize()}%"


def wrap(text: str, indent: str = "  ") -> str:
    """Wrap each line separately so bullets and line breaks survive.
    Bullet lines get a hanging indent; URLs are never split."""
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", text)  # markdown links
    lines = []
    for line in text.splitlines() or [""]:
        line = line.strip()
        if not line:
            lines.append("")
            continue
        hanging = indent + "  " if line[:1] in ("•", "-", "*", "!") else indent
        lines.append(textwrap.fill(line, WIDTH, initial_indent=indent, subsequent_indent=hanging,
                                   break_long_words=False, break_on_hyphens=False))
    return "\n".join(lines)


def field(name: str, text: str) -> str:
    """'  Name:          value' with continuation lines aligned under the value."""
    prefix = f"  {name + ':':<16}"
    return textwrap.fill(text, WIDTH, initial_indent=prefix,
                         subsequent_indent=" " * len(prefix),
                         break_long_words=False, break_on_hyphens=False)


def label(key: str | None) -> str:
    return (key or "").replace("_", " ").lower()


# --- Top-level render ----------------------------------------------------------

def render(result: CheckResult, raw: bool = False, *,
           summary: EligibilitySummary | None = None) -> str:
    body = result.body or {}
    if summary is None:
        summary = parse_eligibility(body)
    errors = errors_from_body(body)

    parts = [_header(result, summary)]
    if errors:
        parts.append(_rejections(explain_all(errors), result))
    elif summary.is_active is True:
        parts.append(_active(summary))
    elif summary.is_active is False:
        parts.append(_inactive(summary))
    else:
        parts.append("COVERAGE: UNKNOWN\n  The response had no coverage status and no "
                     "rejection. Use --raw to inspect it.")
    if raw:
        parts.append(render_raw(body.get("x12")))
    return "\n\n".join(parts)


def _header(result: CheckResult, s: EligibilitySummary) -> str:
    source = "saved response (offline)" if result.source == "fixture" else "live"
    attempts = f", {result.attempts} attempts" if result.attempts > 1 else ""
    lines = [
        "=" * WIDTH,
        f"Eligibility check {s.check_id or ''}".rstrip(),
        f"Source: {source}{attempts} | HTTP {result.http_status}",
        RULE,
        f"Payer:    {s.payer_name or '-'}",
        f"Patient:  {s.patient_name or '-'}",
        f"Member:   {s.member_id or '-'}" + (f"    Group: {s.group_number}" if s.group_number else ""),
    ]
    return "\n".join(lines)


# --- Coverage views -------------------------------------------------------------

def _active(s: EligibilitySummary) -> str:
    plan = s.plan_name or "plan name not provided"
    details = ", ".join(x for x in [
        label(s.insurance_type) if s.insurance_type else None,
        f"since {s.plan_start}" if s.plan_start else None,
    ] if x)
    out = [f"COVERAGE: ACTIVE  ({s.coverage_status})",
           f"  Plan: {plan}" + (f" ({details})" if details else "")]
    if s.prior_auth == "REQUIRED":
        out.append("  Prior authorization: required on this plan for some services")
    for message in s.plan_messages:
        out.append(f"  Payer note: {message}")

    out += ["", _plan_level_costs(s), "", _visit_costs(s)]
    warnings = data_warnings(s)
    if warnings:
        out += ["", "Data notes"] + [wrap(f"! {w}") for w in warnings]
    if s.plan_count > 1:
        out += ["", f"  ! The payer returned {s.plan_count} plans; this summary shows the first."]
    if s.invalid_entry_count:
        out += ["", f"  ! {s.invalid_entry_count} benefit entries didn't match the X12 spec "
                    "and were left out. See --raw."]
    return "\n".join(out)


def _inactive(s: EligibilitySummary) -> str:
    return "\n".join([
        f"COVERAGE: INACTIVE  ({s.coverage_status})",
        "  The payer reports no active coverage for this patient on the date of service.",
        "  Next step: ask the patient for current insurance. If they don't know it, an",
        "  insurance discovery check can search for active coverage.",
    ])


def _plan_level_costs(s: EligibilitySummary) -> str:
    rows = [c for c in s.cost_shares
            if c.kind in ("deductible", "out_of_pocket") and c.service_code in ("30", None)]
    if not rows:
        return "Plan-level costs\n  The payer didn't report a deductible or out-of-pocket maximum."

    out = []
    networks = OrderedDict((c.network, None) for c in rows)
    for network in networks:
        out.append(f"Plan-level costs ({NETWORK_LABELS.get(network, label(network))})")
        in_net = [c for c in rows if c.network == network]
        keys = OrderedDict()
        for kind in ("deductible", "out_of_pocket"):
            periods = [c.time_period for c in in_net if c.kind == kind]
            for period in sorted(set(periods), key=lambda p: (
                    PERIOD_ORDER.index(p) if p in PERIOD_ORDER else 99, p or "")):
                keys[(kind, period)] = None
        for kind, period in keys:
            name = PLAN_LEVEL_LABELS.get(
                (kind, period), f"{label(kind).capitalize()} ({label(period)})")
            cells = []
            for level in ("INDIVIDUAL", "FAMILY"):
                match = [c for c in in_net if (c.kind, c.time_period, c.coverage_level)
                         == (kind, period, level)]
                if match:
                    cells.append(f"{level.capitalize():<11}{money(match[0].amount):>9}")
            out.append(f"  {name:<28}" + "    ".join(cells))
    return "\n".join(out)


def _visit_costs(s: EligibilitySummary) -> str:
    groups: "OrderedDict[tuple, dict]" = OrderedDict()
    for c in s.cost_shares:
        if c.kind not in ("copay", "coinsurance"):
            continue
        key = (c.service_code, c.service_name, tuple(c.messages), c.network)
        groups.setdefault(key, {})[c.kind] = c

    if not groups:
        return "Visit costs\n  The payer didn't report copays or coinsurance."

    def sort_key(item):
        (code, name, messages, _), _ = item
        rank = SERVICE_ORDER.index(code) if code in SERVICE_ORDER else len(SERVICE_ORDER)
        return (rank, name or "", len(messages), messages)

    out = ["Visit costs",
           f"  {'Service':<32}{'Copay':>7}  {'Coins.':>6}  Notes",
           f"  {'-' * 32}{'-' * 7}  {'-' * 6}  {'-' * 30}"]
    previous_service = None
    for (code, name, messages, network), kinds in sorted(groups.items(), key=sort_key):
        service = name or code or "-"
        shown = service if service != previous_service else ""
        previous_service = service
        notes = list(messages)
        if network not in ("IN_NETWORK", None):
            notes.append(f"({NETWORK_LABELS.get(network, label(network))})")
        copay = money(kinds["copay"].amount) if "copay" in kinds else "-"
        coins = percent(kinds["coinsurance"].percent) if "coinsurance" in kinds else "-"
        row = f"  {shown[:32]:<32}{copay:>7}  {coins:>6}  "
        note_lines = textwrap.wrap("; ".join(notes), WIDTH - len(row)) or [""]
        out.append((row + note_lines[0]).rstrip())
        out += [" " * len(row) + extra for extra in note_lines[1:]]
    out.append("  Same service on two lines = the payer sent two amounts; the note says which applies.")
    return "\n".join(out)


def data_warnings(s: EligibilitySummary) -> list[str]:
    """Flag payer figures that contradict each other. We show them as sent and
    never 'fix' them, but a careful reader should know."""
    warnings = []
    for kind in ("deductible", "out_of_pocket"):
        for level in ("INDIVIDUAL", "FAMILY"):
            for network in {c.network for c in s.cost_shares}:
                def amount(period):
                    m = [c for c in s.find(kind, None, level, period) if c.network == network]
                    try:
                        return Decimal(m[0].amount) if m and m[0].amount else None
                    except InvalidOperation:
                        return None
                total, used, left = amount("CALENDAR_YEAR"), amount("YEAR_TO_DATE"), amount("REMAINING")
                if None not in (total, used, left) and used + left != total:
                    warnings.append(
                        f"{level.capitalize()} {label(kind).replace(' ', '-')} figures don't add up: "
                        f"{money(str(used))} spent + {money(str(left))} remaining != "
                        f"{money(str(total))} max. Shown as the payer reported them."
                    )
    return warnings


# --- Rejection view --------------------------------------------------------------

def _rejections(explanations: list[Explanation], result: CheckResult) -> str:
    out = []
    if result.http_status == 400:
        out.append("Note: Stedi returned HTTP 400, but the body still contains the payer-level "
                   "error below.")
    for e in explanations:
        retry = ("Yes, automatically (payer is temporarily unavailable)" if e.retryable
                 else "No")
        if e.retryable and result.attempts > 1:
            retry = f"Tried {result.attempts} times; the payer still didn't respond. Try again later."
        if e.followup_action:
            retry += f" (payer says: {e.followup_action})"
        out += [
            f"REJECTED BY PAYER  AAA {e.code}: {e.payer_description}",
            field("What it means", e.meaning),
            field("Who acts", e.who_acts),
            field("Next step", e.next_step),
            field("Retry", retry),
            field("Where", f"{e.location or '-'} loop of the 271"),
        ]
        if e.possible_resolutions:
            out += ["  Stedi's guidance:", wrap(e.possible_resolutions, indent="    ")]
        out.append("")
    return "\n".join(out).rstrip()


# --- Raw X12 view ----------------------------------------------------------------

EB01 = {"1": "Active Coverage", "6": "Inactive", "A": "Co-Insurance", "B": "Co-Payment",
        "C": "Deductible", "G": "Out of Pocket (Stop Loss)", "L": "Primary Care Provider",
        "N": "Services Restricted to Following Provider", "U": "Contact Following Entity",
        "X": "Health Care Facility", "R": "Other or Additional Payer", "D": "Benefit Description",
        "F": "Limitations"}
EB02 = {"IND": "individual", "FAM": "family", "EMP": "employee only", "ESP": "employee and spouse",
        "ECH": "employee and children", "CHD": "children only", "DEP": "dependents only",
        "SPC": "spouse and children", "SPO": "spouse only"}
EB06 = {"21": "years", "22": "service year", "23": "calendar year", "24": "year to date",
        "25": "contract", "26": "episode", "27": "visit", "29": "remaining", "32": "lifetime",
        "33": "lifetime remaining", "34": "month", "35": "week", "36": "admission"}
EB12 = {"Y": "in network", "N": "out of network", "W": "network n/a", "U": "network unknown"}
STC = {"1": "medical care", "30": "plan coverage", "33": "chiropractic", "35": "dental",
       "47": "hospital", "48": "hospital inpatient", "50": "hospital outpatient",
       "86": "emergency", "88": "pharmacy", "96": "professional (physician)",
       "AL": "vision", "BY": "office visit: sick", "BZ": "office visit: well",
       "MH": "mental health", "PT": "physical therapy", "UC": "urgent care"}
NM1 = {"PR": "Payer", "1P": "Provider", "IL": "Subscriber", "03": "Dependent",
       "P3": "Primary care provider", "VN": "Vendor", "FA": "Facility",
       "13": "Contracted service provider"}


def render_raw(x12: str | None) -> str:
    if not x12 or not x12.startswith("ISA") or len(x12) < 106:
        return "Raw X12\n  No X12 in this response."
    element_sep = x12[3]
    segment_end = x12[105]  # ISA is fixed-width: the terminator follows it
    repeat_sep = x12.split(element_sep)[11] if len(x12.split(element_sep)) > 11 else "^"

    out = ["Raw X12 271 (key segments, annotated)", RULE]
    for segment in x12.split(segment_end):
        segment = segment.strip()
        e = segment.split(element_sep)
        tag = e[0]
        if tag == "NM1":
            name = " ".join(x for x in [e[4] if len(e) > 4 else "", e[3] if len(e) > 3 else ""] if x)
            note = f"{NM1.get(e[1], e[1])}: {name}"
        elif tag == "EB":
            note = _annotate_eb(e, repeat_sep)
        elif tag == "AAA":
            code = e[3] if len(e) > 3 else "?"
            followup = e[4] if len(e) > 4 else ""
            note = f"REJECTION code {code}" + (
                f" | follow-up {followup} = {FOLLOWUP_TEXT.get(followup, 'unknown')}"
                if followup else "")
        elif tag == "MSG":
            note = "payer message for the line above"
        else:
            continue
        out.append(f"  {segment}")
        out.append(f"      -> {note}")
    return "\n".join(out)


def _annotate_eb(e: list[str], repeat_sep: str) -> str:
    def get(i):
        return e[i] if len(e) > i else ""
    parts = [EB01.get(get(1), f"EB01={get(1)}")]
    if get(2):
        parts.append(EB02.get(get(2), get(2)))
    if get(3):
        parts.append(", ".join(STC.get(c, c) for c in get(3).split(repeat_sep)))
    if get(5):
        parts.append(f'plan "{get(5)}"')
    if get(6):
        parts.append(EB06.get(get(6), f"period {get(6)}"))
    if get(7):
        parts.append(money(get(7)))
    if get(8):
        parts.append(percent(get(8)))
    if get(12):
        parts.append(EB12.get(get(12), get(12)))
    return " | ".join(parts)
