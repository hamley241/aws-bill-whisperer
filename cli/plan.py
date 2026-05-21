#!/usr/bin/env python3
"""
`whisper-plan` — run the SavingsPlanner against a findings file.

Usage:

    whisper-plan path/to/findings.json --goal "cut 20%"
    whisper-plan path/to/findings.json --goal "..." --no-trace

Output is the PlanResult as JSON (canonical schema). The PlanRecord is
written to ~/.whisper/whisper.db unless --no-trace.

This is a spike-grade CLI; the full Slack-side surface comes later.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent.planner import SavingsPlanner  # noqa: E402
from agent.evals.runner import _finding_from_dict  # noqa: E402 — internal reuse
from config import load_config  # noqa: E402
from llm import make_llm_client  # noqa: E402
from storage import WhisperRepository, default_repository  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="whisper-plan",
        description="Run the SavingsPlanner over a JSON list of findings.",
    )
    parser.add_argument("findings_path", type=Path,
                        help="Path to a JSON file with a list of Finding dicts.")
    parser.add_argument("--goal", default=None,
                        help="Free-form goal string passed to the planner.")
    parser.add_argument("--scan-id", default=None,
                        help="Optional scan_id grouping for the audit log.")
    parser.add_argument("--actor", default=None,
                        help="Actor recorded in the audit log (e.g. your username).")
    parser.add_argument("--no-trace", action="store_true",
                        help="Don't write a PlanRecord to the audit log.")
    args = parser.parse_args(argv)

    raw = json.loads(args.findings_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        print(f"{args.findings_path}: expected a JSON list", file=sys.stderr)
        return 2
    findings = [_finding_from_dict(f) for f in raw]

    config = load_config()
    repository: WhisperRepository | None
    repository = None if args.no_trace else default_repository()

    try:
        llm = make_llm_client(config, prompt_template="savings_plan")
    except ValueError as e:
        print(f"LLM not configured: {e}", file=sys.stderr)
        print("Run `whisper-config doctor` to see what's missing.", file=sys.stderr)
        return 1

    planner = SavingsPlanner(llm=llm, repository=repository)
    result = planner.plan(
        findings, goal=args.goal, scan_id=args.scan_id, actor=args.actor,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
