"""Command-line demo for Stedi's real-time eligibility API (X12 270/271).

  python cli.py list                       show the mock scenarios
  python cli.py check uhc-active           run one live check (test mode)
  python cli.py check aaa-75 --raw         also show the X12 271 behind it
  python cli.py check uhc-active --offline use the saved response, no network

All scenarios are Stedi test-mode mock data. No real patient information.
"""
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from eligibility.client import AuthError, EligibilityClient, RequestRejected, StediError, TransientError
from eligibility.display import render
from eligibility.scenarios import SCENARIOS
from eligibility.workflow import fixture_path, load_fixture, run_check
from eligibility.parser import parse_eligibility


def cmd_list() -> int:
    print(f"{'Scenario':<15}{'Saved':<7}Description")
    for name, scenario in SCENARIOS.items():
        saved = "yes" if fixture_path(name).exists() else ""
        print(f"{name:<15}{saved:<7}{scenario.description}")
    print("\n'Saved' scenarios also work with --offline.")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    scenario = SCENARIOS[args.scenario]

    if args.offline:
        try:
            result = load_fixture(args.scenario)
        except FileNotFoundError as exc:
            print(exc, file=sys.stderr)
            return 1
    else:
        load_dotenv()

        def on_retry(attempt: int, delay: float, errors: list) -> None:
            codes = ", ".join(e.get("code", "?") for e in errors)
            print(f"  Payer temporarily unavailable (AAA {codes}). "
                  f"Attempt {attempt} failed; retrying in {delay:.0f}s...", file=sys.stderr)

        try:
            with EligibilityClient() as client:
                result = run_check(client, scenario.request,
                                   retry_seconds=args.retry_seconds, on_retry=on_retry)
        except AuthError as exc:
            print(f"Authentication problem: {exc}", file=sys.stderr)
            return 2
        except TransientError as exc:
            print(f"Stedi or the payer is unavailable: {exc}", file=sys.stderr)
            return 3
        except RequestRejected as exc:
            print(f"Request rejected: {exc}", file=sys.stderr)
            return 4
        except StediError as exc:
            print(f"Unexpected error: {exc}", file=sys.stderr)
            return 1

    summary = parse_eligibility(result.body or {})
    print(render(result, raw=args.raw, summary=summary))
    return 0


def main(argv: list[str] | None = None) -> int:
    # Payer text can include characters a Windows console can't show; don't crash on them.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(
        prog="cli.py", description="Stedi real-time eligibility demo (test mode).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="show available mock scenarios")

    check = sub.add_parser("check", help="run an eligibility check")
    check.add_argument("scenario", choices=list(SCENARIOS), help="scenario name (see 'list')")
    check.add_argument("--raw", action="store_true", help="also show the annotated X12 271")
    check.add_argument("--offline", action="store_true",
                       help="use the saved response in fixtures/ instead of calling Stedi")
    check.add_argument("--retry-seconds", type=float, default=120.0,
                       help="how long to keep retrying when the payer is down (default 120)")

    args = parser.parse_args(argv)
    return cmd_list() if args.command == "list" else cmd_check(args)


if __name__ == "__main__":
    sys.exit(main())
