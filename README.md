# Stedi Eligibility Demo

A small Python CLI that runs real-time insurance eligibility checks through
[Stedi's](https://www.stedi.com) healthcare API and explains the answer in plain
English: is the patient covered, what will they owe, and if the payer said no,
who needs to fix it.

Under the hood, each check is an **X12 270 eligibility request** sent as JSON;
the payer's answer comes back as an **X12 271 response**, which Stedi returns
as both JSON and raw X12. This project parses that response, handles failures
the way Stedi recommends, and can show the raw X12 segments behind every
summary.

> **Test mode only.** Every request uses Stedi's predefined mock data. No real
> patient information is used or stored.

---

## What it looks like

```text
$ python cli.py check uhc-active

COVERAGE: ACTIVE  (ACTIVE_COVERAGE)
  Plan: Gold Plan HMO (commercial, since 2024-01-01)
  Prior authorization: required on this plan for some services
  Payer note: PCP TO SUBMIT A SPECIALIST REFERRAL

Plan-level costs (in network)
  Deductible remaining        Individual        $0    Family            $0
  Out-of-pocket max (annual)  Individual    $1,500    Family        $3,000
  Out-of-pocket spent so far  Individual      $650    Family          $925
  Out-of-pocket remaining     Individual      $850    Family        $2,200

Visit costs
  Service                           Copay  Coins.  Notes
  Physician Visit - Office: Sick      $15      0%
  Physician Visit - Office: Well      $15      0%
                                       $0      0%  OUTPATIENT PERIODIC HEALTH EVALUATION
  Emergency Services                  $50      0%  COPAY WAIVED IF ADMITTED
  ...

Data notes
  ! Family out-of-pocket figures don't add up: $925 spent + $2,200 remaining != $3,000
    max. Shown as the payer reported them.
```

```text
$ python cli.py check aaa-75

REJECTED BY PAYER  AAA 75: Subscriber/Insured Not Found
  What it means:  The payer searched and couldn't find this person at all.
  Who acts:       Front desk / patient intake
  Next step:      Check the patient's insurance card and re-enter member ID, name
                  (exactly as printed) and date of birth.
  Retry:          No (payer says: Resubmission Not Allowed)
  Where:          SUBSCRIBER loop of the 271
  Stedi's guidance:
    ...
```

```text
$ python cli.py check uhc-active --raw      # summary plus annotated X12

  EB*B*IND*BY^BZ^UC***27*15*****Y
      -> Co-Payment | individual | office visit: sick, office visit: well, urgent care | visit | $15 | in network
  AAA*N**75*N
      -> REJECTION code 75 | follow-up N = Resubmission Not Allowed
```

---

## Quick start

Requires Python 3.10+ and a free [Stedi sandbox account](https://portal.stedi.com).

```bash
git clone https://github.com/edgnunez/stedi-eligibility-demo.git
cd stedi-eligibility-demo
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env
```

In the Stedi portal, create an API key with **Mode: Test** and paste it into
`.env` (`STEDI_API_KEY=...`). `.env` is gitignored.

```bash
python cli.py list                         # available mock scenarios
python cli.py check uhc-active             # live check against Stedi test mode
python cli.py check aaa-75                 # a payer rejection, explained
python cli.py check uhc-active --raw       # also show the annotated X12 271
python cli.py check aaa-42 --retry-seconds 10   # watch the retry loop
python cli.py check uhc-active --offline   # saved response, no network or key
pytest                                     # 56 tests, no network needed
```

---

## Where this fits in healthcare billing

An eligibility check is the first step in getting a medical visit paid:

| Step | Question | X12 transaction |
|---|---|---|
| **Eligibility** | Is the patient covered, and what will they owe? | **270 / 271** |
| Claim | Here's what we did; please pay us | 837 |
| Claim status | Where is my claim? | 276 / 277 |
| Remittance (ERA) | Here's what we paid, and why | 835 |

Bad eligibility data leads to denied claims later, so it's usually where a
clinic or billing company starts. A clearinghouse like Stedi sits between
providers and payers: you send JSON, Stedi translates it to X12, routes it to
the right insurer, and translates the answer back.

---

## Architecture

```text
cli.py                     argument parsing, exit codes
  │
  ├── eligibility/scenarios.py   named mock requests (Stedi's test-mode values)
  ├── eligibility/workflow.py    run a check: retry loop for payer outages, offline fixtures
  │     └── eligibility/client.py    HTTP only: auth, timeouts, HTTP-level retries, typed errors
  ├── eligibility/parser.py      271 JSON -> EligibilitySummary
  ├── eligibility/rejections.py  AAA codes -> meaning, who acts, retry or not
  └── eligibility/display.py     summary, rejection view, annotated raw X12

fixtures/                  real Stedi test-mode responses, used by tests and --offline
tests/                     56 tests: client (fake network), parser, rejections,
                           workflow (fake clock), display, recorded responses
record_fixtures.py         re-records fixtures from the live API
```

Each module has one job. The client knows HTTP but nothing about healthcare;
the parser knows the 271 but nothing about HTTP; the display knows formatting
but makes no decisions. That separation is what makes each piece testable on
its own.

**Endpoint:** `POST https://healthcare.us.stedi.com/2026-06-01/eligibility-check`,
Stedi's redesigned JSON eligibility endpoint, which groups benefits by plan
and gives each benefit type its own schema. Authentication is the API key in
the `Authorization` header.

---

## Design choices and trade-offs

**1. Two kinds of failure, handled in two places.**
An HTTP failure (bad key, rate limit, outage) means the request didn't get an
answer. A payer rejection, an X12 `AAA` error such as "member not found", is a
*successful* API call that comes back HTTP 200 with an `errors` array: the
insurer answered "no." The client raises typed exceptions for the first kind
and returns the second kind as data. Mixing them up is the most common
integration mistake, and there's a test pinning the distinction.

**2. A 400 can still be an answer.** An unknown payer ID returns HTTP 400 with
an AAA 79 inside the body. Rather than reporting "400 Bad Request", the client
keeps the body and the CLI explains the payer-level error.

**3. Rejections are routed to whoever can fix them.** Knowing that code 72
means "invalid member ID" isn't the useful part; knowing who acts is. Every AAA
code maps to one of five owners: the front desk (patient data), the provider's
enrollment team (NPI not registered), the integration developer (request
content), nobody (payer temporarily down, retry), or Stedi support (payer
connectivity). Stedi's own `possibleResolutions` text is shown alongside.
Unmapped codes fall back to the payer's wording and escalate.

**4. Retries follow Stedi's guidance, with backoff.** Only AAA 42 and 80 are
retried, for up to ~2 minutes as Stedi's troubleshooting guide recommends for
real-time checks, waiting 2s, 4s, 8s... rather than hammering a struggling
payer. Code 42 with a follow-up of "Correct and Resubmit", "Resubmission Not
Allowed", or a third-party inquiry is *not* retried: the payer is saying the
request itself is the problem. Timeouts aren't retried automatically because
every production check is a billed transaction.

**5. Report payer data; never "fix" it.** In the mock response the family
out-of-pocket figures don't add up ($925 + $2,200 ≠ $3,000). The parser never
derives one figure from others, and the display flags the inconsistency
instead of hiding it.

**6. Keep the payer's notes.** The well-visit service has two copays, $0 and
$15. The only thing distinguishing them is the payer message "OUTPATIENT
PERIODIC HEALTH EVALUATION." The raw X12 shows why: the $15 comes from one
benefit line covering three services (`EB*B*IND*BY^BZ^UC...`), which Stedi
splits into separate JSON entries, and the $0 from another. Dropping messages
would turn correct data into a wrong price.

**7. Built from real responses, not assumptions.** The first step was a smoke
test that saved a real response; the parser was written against it. That
surfaced details the docs didn't spell out, like the plan name arriving in
`planCoverageDescription` instead of `plans[].name`.

**8. Plain `httpx` instead of Stedi's Python SDK.** The goal was to show the
integration from the HTTP level up. In production I'd evaluate the SDK, which
provides typed models and retries out of the box.

**9. Offline mode for reliable demos.** `--offline` renders saved responses,
so a demo doesn't depend on the network.

### Known limitations

- Shows the first plan when a payer returns several (it says so when that happens).
- Displays STC 30 plan-level costs and per-service copays/coinsurance; benefit
  limits, service-level deductibles, and dependent-specific views aren't covered.
- The raw view annotates the segments a reader needs most (`NM1`, `EB`, `AAA`,
  `MSG`), not every element, e.g. it doesn't annotate EB11 (authorization required).
- Test mode only.

---

## How it would scale

The CLI checks one patient on demand. A clinic or billing company runs
thousands of checks a day, mostly the night before appointments. At that
scale I'd change:

- **Batch endpoint for scheduled checks.** Stedi offers a Batch Eligibility
  Check API, and its troubleshooting guide recommends a different retry policy
  for scheduled checks than for real-time ones (longer, exponential backoff
  over hours). Real-time stays for check-in and walk-ins.
- **A queue and workers.** Put checks on a queue, run workers with a
  concurrency limit below the account's limit, and treat HTTP 429 as a signal
  to slow down. The client already honors `Retry-After`.
- **Store every result.** Write the summary and raw response to a database
  (Postgres here) keyed by Stedi's check `id`, for audit and for re-display
  without re-paying for the check. The `externalPatientId` request field lets
  Stedi correlate checks for the same patient.
- **Track rejections as a metric.** A rising share of AAA 72/73/75 points to an
  intake problem at a specific clinic; a spike in 43 points to provider
  enrollment; 42/79 point to payer connectivity. That's a dashboard (Metabase)
  an operations or implementation team would watch.
- **PHI handling.** Production means real patient data: encryption at rest,
  access controls, log scrubbing (never log request bodies), and retention
  rules. Test mode sidesteps all of this, which is part of why it's used here.

---

## Tests

```bash
pytest
```

56 tests, none of which touch the network:

- `test_client.py`: auth header, 401/400/429/503 handling, retries (fake transport)
- `test_parser.py`: parsing the real active-coverage response
- `test_recorded_responses.py`: real inactive, rejection, and unknown-payer responses
- `test_rejections.py`: routing and retry rules for AAA codes
- `test_workflow.py`: the retry window, tested in milliseconds with a fake clock
- `test_display.py`: what a person actually sees

To refresh the saved responses from the live API: `python record_fixtures.py`.

---

## References

- [Stedi Real-Time Eligibility Check API](https://www.stedi.com/docs/healthcare/api-reference/post-eligibility-check)
- [Eligibility mock requests (test mode)](https://www.stedi.com/docs/healthcare/api-reference/mock-requests-eligibility-checks)
- [Eligibility troubleshooting and AAA errors](https://www.stedi.com/docs/healthcare/eligibility-troubleshooting)
- [Migrating to the redesigned eligibility endpoint](https://www.stedi.com/blog/migrate-to-the-redesigned-json-real-time-eligibility-check-api)

Related project: [iot-redpanda-pipeline](https://github.com/edgnunez/iot-redpanda-pipeline),
a streaming pipeline (Python, Redpanda, Postgres, Metabase, Docker Compose).
