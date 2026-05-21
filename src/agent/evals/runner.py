"""
Eval runner — loads a fixture, drives SavingsPlanner against either a
replay LLM (default) or the live LLM (--re-record), applies the rubric,
prints results.

Fixture layout:

    src/agent/evals/fixtures/<scenario>/
        findings.json            input list[Finding-dict]
        assertions.yaml          rubric
        recorded_response.json   { "responses": ["...", "..."], "metadata": {...} }
        goal                     optional plain-text file: the goal string

The replay LLM consumes responses[] in order. Most fixtures need exactly
one; fixtures that exercise the parse-retry path need two.

Replay is the default. Re-recording requires an explicit flag and the
runner refuses to re-record unless `WHISPER_ALLOW_REAL_LLM=1` is set —
double-locking to prevent accidental live calls from CI.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

_SRC = Path(__file__).parent.parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent.planner import SavingsPlanner
from agent.schemas import PlanResult
from llm.base import LLMClient, LLMResponse, Message
from patterns.base import Finding, RiskTier

from .rubric import CheckResult, load_rubric, run_rubric

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

@dataclass
class Fixture:
    name: str
    findings: list[Finding]
    assertions: list[dict[str, Any]]
    recorded_responses: list[str]
    goal: str | None
    dir: Path

    @property
    def recording_path(self) -> Path:
        return self.dir / "recorded_response.json"


def load_fixture(scenario: str | Path) -> Fixture:
    fixture_dir = _resolve_dir(scenario)
    findings_path = fixture_dir / "findings.json"
    assertions_path = fixture_dir / "assertions.yaml"

    if not findings_path.exists():
        raise FileNotFoundError(f"{findings_path} missing")
    if not assertions_path.exists():
        raise FileNotFoundError(f"{assertions_path} missing")

    findings = _load_findings(findings_path)
    assertions = load_rubric(assertions_path)
    goal = (fixture_dir / "goal").read_text(encoding="utf-8").strip() \
        if (fixture_dir / "goal").exists() else None

    recording_path = fixture_dir / "recorded_response.json"
    if recording_path.exists():
        recorded = json.loads(recording_path.read_text(encoding="utf-8"))
        responses = recorded.get("responses", [])
    else:
        responses = []

    return Fixture(
        name=fixture_dir.name,
        findings=findings,
        assertions=assertions,
        recorded_responses=responses,
        goal=goal,
        dir=fixture_dir,
    )


def _resolve_dir(scenario: str | Path) -> Path:
    p = Path(scenario)
    if p.is_absolute() or p.exists():
        return p
    return FIXTURES_DIR / scenario


def _load_findings(path: Path) -> list[Finding]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected list of finding dicts")
    out: list[Finding] = []
    for raw in data:
        out.append(_finding_from_dict(raw))
    return out


def _finding_from_dict(raw: dict) -> Finding:
    """Reconstruct a Finding from the fixture-on-disk representation.

    Fixtures use the same shape as Finding.to_dict(), with risk_tier as
    a string. We coerce back to the enum and only pass kwargs the
    dataclass accepts.
    """
    accepted = {
        "id", "schema_version", "pattern_id", "resource_id", "resource_type",
        "resource_arn", "account_id", "region", "monthly_impact_usd",
        "confidence", "summary", "explanation", "fix_command", "fix_pr",
        "evidence", "metadata", "safe_to_fix",
    }
    kwargs = {k: v for k, v in raw.items() if k in accepted}
    if "risk_tier" in raw:
        kwargs["risk_tier"] = RiskTier(raw["risk_tier"])
    return Finding(**kwargs)


# ---------------------------------------------------------------------------
# Replay + record LLMs
# ---------------------------------------------------------------------------

class _ReplayLLM(LLMClient):
    """Returns the next scripted response. Raises on overrun."""
    provider = "replay"
    boundary_crossed = False

    def __init__(self, responses: list[str], *, model: str = "recorded"):
        if not responses:
            raise ValueError(
                "No recorded responses available. Run with --re-record to "
                "create the recording, or check that recorded_response.json "
                "is present in the fixture directory."
            )
        self._script = list(responses)
        self._model = model

    def complete(self, messages, *, model=None, max_tokens=4096, temperature=0.0):
        if not self._script:
            raise AssertionError(
                "Replay LLM exhausted — recording has fewer responses than "
                "the planner needed. Re-record the fixture."
            )
        text = self._script.pop(0)
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=self._model,
            boundary_crossed=False,
        )

    @property
    def default_model(self):
        return self._model


class _RecordingLLM(LLMClient):
    """Wraps a real LLM and stores every response."""

    def __init__(self, inner: LLMClient):
        self._inner = inner
        self.provider = inner.provider
        self.boundary_crossed = inner.boundary_crossed
        self.recorded: list[str] = []

    def complete(self, messages, *, model=None, max_tokens=4096, temperature=0.0):
        resp = self._inner.complete(
            messages, model=model, max_tokens=max_tokens, temperature=temperature,
        )
        self.recorded.append(resp.text)
        return resp

    @property
    def default_model(self):
        return self._inner.default_model


# ---------------------------------------------------------------------------
# Eval execution
# ---------------------------------------------------------------------------

@dataclass
class EvalResult:
    fixture: str
    plan: PlanResult
    checks: list[CheckResult]
    parse_retry_count: int = 0
    rerecorded: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and all(c.ok for c in self.checks)


def run_fixture(
    scenario: str | Path,
    *,
    re_record: bool = False,
    live_llm: LLMClient | None = None,
) -> EvalResult:
    """Run one fixture. Returns an EvalResult.

    Replay (default): reads recorded_response.json, drives planner.
    Re-record: requires `re_record=True` AND env `WHISPER_ALLOW_REAL_LLM=1`.
               Calls the live LLM, writes the responses to the fixture
               directory, then applies the rubric to the live result.
    """
    fixture = load_fixture(scenario)

    if re_record:
        if os.environ.get("WHISPER_ALLOW_REAL_LLM") != "1":
            return EvalResult(
                fixture=fixture.name,
                plan=_empty_plan(),
                checks=[],
                errors=[
                    "refusing to re-record without WHISPER_ALLOW_REAL_LLM=1. "
                    "This double-lock prevents accidental live-LLM calls."
                ],
            )
        if live_llm is None:
            from config import load_config
            from llm import make_llm_client
            live_llm = make_llm_client(load_config(), prompt_template="savings_plan")
        recorder = _RecordingLLM(live_llm)
        planner = SavingsPlanner(llm=recorder)
        plan = planner.plan(fixture.findings, goal=fixture.goal)
        _write_recording(fixture.recording_path, recorder.recorded, plan)
        return EvalResult(
            fixture=fixture.name,
            plan=plan,
            checks=run_rubric(fixture.assertions, plan, fixture.findings),
            parse_retry_count=plan.parse_retry_count,
            rerecorded=True,
        )

    # Replay path.
    replay = _ReplayLLM(fixture.recorded_responses)
    planner = SavingsPlanner(llm=replay)
    plan = planner.plan(fixture.findings, goal=fixture.goal)
    return EvalResult(
        fixture=fixture.name,
        plan=plan,
        checks=run_rubric(fixture.assertions, plan, fixture.findings),
        parse_retry_count=plan.parse_retry_count,
    )


def _write_recording(path: Path, responses: list[str], plan: PlanResult) -> None:
    payload = {
        "responses": responses,
        "metadata": {
            "model": plan.model,
            "provider": plan.provider,
            "boundary_crossed": plan.boundary_crossed,
            "parse_retry_count": plan.parse_retry_count,
            "prompt_template": plan.prompt_template,
            "prompt_template_version": plan.prompt_template_version,
        },
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8")


def _empty_plan() -> PlanResult:
    from agent.schemas import new_plan_id
    return PlanResult(
        plan_id=new_plan_id(),
        goal=None,
        status="validation_failed",
        steps=[],
        dropped_steps=[],
        total_monthly_impact_usd=0.0,
        summary="(no plan produced)",
        confidence=0.0,
        prompt_template="savings_plan",
        prompt_template_version="v1",
        model="none",
        provider="none",
        boundary_crossed=False,
        parse_retry_count=0,
        input_finding_ids=[],
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-evals",
        description="Run SavingsPlanner eval fixtures (record/replay).",
    )
    parser.add_argument(
        "scenario", nargs="?", default=None,
        help="fixture name (e.g. p001_only) or path. Omit to run all.",
    )
    parser.add_argument(
        "--re-record", action="store_true",
        help="Re-record this fixture against the live LLM. Requires "
             "WHISPER_ALLOW_REAL_LLM=1 in the environment.",
    )
    args = parser.parse_args(argv)

    scenarios = [args.scenario] if args.scenario else _all_scenarios()
    if not scenarios:
        print("no fixtures found in", FIXTURES_DIR, file=sys.stderr)
        return 1

    overall_ok = True
    for scenario in scenarios:
        result = run_fixture(scenario, re_record=args.re_record)
        _print_result(result)
        if not result.ok:
            overall_ok = False
    return 0 if overall_ok else 1


def _all_scenarios() -> list[str]:
    if not FIXTURES_DIR.exists():
        return []
    return sorted(p.name for p in FIXTURES_DIR.iterdir() if p.is_dir())


def _print_result(result: EvalResult) -> None:
    marker = "PASS" if result.ok else "FAIL"
    print(f"[{marker}] {result.fixture}")
    if result.rerecorded:
        print("  (recording refreshed)")
    for c in result.checks:
        line_marker = "  ✓" if c.ok else "  ✗"
        print(f"{line_marker} {c.assertion_type}: {c.detail}")
    for err in result.errors:
        print(f"  ! {err}")


if __name__ == "__main__":
    sys.exit(main())
