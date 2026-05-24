#!/usr/bin/env python3
"""
`whisper-plan` — run the SavingsPlanner against a findings file.

Usage:

    whisper-plan path/to/findings.json
    whisper-plan path/to/findings.json --goal "cut 20% this month"
    whisper-plan path/to/findings.json --format json
    whisper-plan path/to/findings.json --json          # alias for --format json
    whisper-plan path/to/findings.json --no-trace

Default output is plain text (greppable, CI-friendly, no colors).
`--format json` (or `--json`) emits the canonical `RenderablePlan`
JSON: a versioned contract surface that downstream tooling can depend
on. The JSON shape carries no renderer-derived fields and no cosmetic
formatting — bump `RENDERABLE_SCHEMA_VERSION` in `src/presenters/plan.py`
for incompatible changes.

Exit codes:
    0   plan status is "ok"
    1   plan status is "validation_failed" (or LLM not configured)
    2   bad input (e.g. findings file is not a JSON list)

The PlanRecord is written to ~/.whisper/whisper.db unless --no-trace.
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

from agent.evals.runner import _finding_from_dict  # noqa: E402 — internal reuse
from agent.planner import SavingsPlanner  # noqa: E402
from config import load_config  # noqa: E402
from llm import make_llm_client  # noqa: E402
from patterns.base import Finding  # noqa: E402
from presenters.plan import (  # noqa: E402
    JSONPlanPresenter,
    TextPlanPresenter,
    to_renderable,
)
from storage import WhisperRepository, default_repository  # noqa: E402


class _BadInput(Exception):
    """Bad-input signal → CLI exit code 2.

    Internal to this module; carries a clean message intended for stderr.
    Wraps the underlying exception so the original traceback is available
    when --no-trace is off and the user wants to diagnose.
    """


def _load_findings(path: Path) -> list[Finding]:
    """Load, decode, and hydrate findings from a JSON file.

    Raises `_BadInput` (caught in main → exit 2) for any of:
      - file missing or unreadable
      - malformed JSON
      - top-level value is not a list
      - an item is not a dict
      - an item is missing required Finding fields or has invalid values
        (e.g. unknown risk_tier)

    Returning a clean error on any of these paths makes the CLI behavior
    match the docstring's exit-code contract.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise _BadInput(f"{path}: file not found") from e
    except OSError as e:
        raise _BadInput(f"{path}: failed to read ({e})") from e

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise _BadInput(f"{path}: malformed JSON ({e})") from e

    if not isinstance(raw, list):
        raise _BadInput(f"{path}: expected a JSON list of finding dicts")

    findings: list[Finding] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise _BadInput(
                f"{path}: item {i} is a {type(item).__name__}, expected dict"
            )
        try:
            findings.append(_finding_from_dict(item))
        except (TypeError, ValueError, KeyError, AttributeError) as e:
            raise _BadInput(
                f"{path}: item {i} failed to hydrate as Finding ({e})"
            ) from e
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="whisper-plan",
        description="Run the SavingsPlanner over a JSON list of findings.",
    )
    parser.add_argument(
        "findings_path", type=Path,
        help="Path to a JSON file with a list of Finding dicts.",
    )
    parser.add_argument(
        "--goal", default=None,
        help="Free-form goal string passed to the planner.",
    )
    parser.add_argument(
        "--scan-id", default=None,
        help="Optional scan_id grouping for the audit log.",
    )
    parser.add_argument(
        "--actor", default=None,
        help="Actor recorded in the audit log (e.g. your username).",
    )
    parser.add_argument(
        "--no-trace", action="store_true",
        help="Don't write a PlanRecord to the audit log.",
    )
    parser.add_argument(
        "--format", dest="output_format", default="text",
        choices=("text", "json"),
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--json", dest="json_alias", action="store_true",
        help="Alias for --format json.",
    )
    args = parser.parse_args(argv)

    try:
        findings = _load_findings(args.findings_path)
    except _BadInput as e:
        print(str(e), file=sys.stderr)
        return 2

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

    renderable = to_renderable(result, findings)
    output_format = "json" if args.json_alias else args.output_format
    if output_format == "json":
        print(JSONPlanPresenter().render(renderable))
    else:
        # TextPlanPresenter already terminates with a newline.
        print(TextPlanPresenter().render(renderable), end="")

    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
