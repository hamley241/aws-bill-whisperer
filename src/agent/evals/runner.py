"""
Eval runner — loads a fixture, drives SavingsPlanner (planner surface)
or the plan-thread answerer (conversation surface) against either a
replay LLM (default) or the live LLM (--re-record), applies the
rubric, prints results.

Two surfaces share one runner to keep the replay/rerecord discipline,
the fixture philosophy, and the assertion-vocabulary registry uniform.
Don't add a sibling runner for any new conversational surface — flag-
extend this one. Conversation evals are NOT soft integration tests;
the same record-replay rules apply.

Planner fixture layout:

    src/agent/evals/fixtures/<scenario>/
        findings.json            input list[Finding-dict]
        assertions.yaml          rubric
        recorded_response.json   { "responses": ["...", "..."], "metadata": {...} }
        goal                     optional plain-text file: the goal string

Conversation fixture layout (--surface conversation):

    src/agent/evals/conversation_fixtures/<scenario>/
        findings.json            input list[Finding-dict] (the scan)
        plan.json                cached PlanResult dict (no LLM call to produce)
        question                 plain-text user question
        assertions.yaml          rubric (uses conversation assertion types)
        recorded_response.json   { "responses": ["..."], "metadata": {...} }
        turn_history.json        optional list of prior turns (default empty)

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
from agent.schemas import (
    CURRENT_SCHEMA_VERSION,
    DroppedStep,
    PlanResult,
    PlanStep,
    SubAction,
)
from llm.base import LLMClient, LLMResponse, Message
from patterns.base import Finding, RiskTier

from .rubric import (
    LEVEL_GATE,
    LEVEL_WARNING,
    CheckResult,
    load_rubric,
    run_conversation_rubric,
    run_rubric,
)

if TYPE_CHECKING:
    from analyzer.plan_conversation import TurnOutcome


logger = logging.getLogger(__name__)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
CONVERSATION_FIXTURES_DIR = Path(__file__).parent / "conversation_fixtures"


SURFACE_PLANNER = "planner"
SURFACE_CONVERSATION = "conversation"
ALL_SURFACES = (SURFACE_PLANNER, SURFACE_CONVERSATION)


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


def _plan_from_dict(raw: dict) -> PlanResult:
    """Reconstruct a PlanResult from a conversation-fixture plan.json.

    Mirrors PlanResult.to_dict() — accepts only the fields a fixture
    is expected to specify. SubAction lists are rebuilt from dicts.
    Missing fields get sensible test defaults (no actor, fresh
    plan_id, etc.).
    """
    steps: list[PlanStep] = []
    for step_dict in raw.get("steps", []):
        sub_actions: list[SubAction] | None = None
        if step_dict.get("recommended_sequence"):
            sub_actions = [
                SubAction(
                    candidate_id=s["candidate_id"],
                    action_kind=s["action_kind"],
                    est_monthly_savings_usd=float(s["est_monthly_savings_usd"]),
                    evidence_tier=s["evidence_tier"],
                    rationale=s.get("rationale", ""),
                )
                for s in step_dict["recommended_sequence"]
            ]
        steps.append(PlanStep(
            finding_id=step_dict["finding_id"],
            pattern_id=step_dict["pattern_id"],
            suggested_mode=step_dict["suggested_mode"],
            monthly_impact_usd=float(step_dict["monthly_impact_usd"]),
            rationale=step_dict.get("rationale", ""),
            order_rank=int(step_dict["order_rank"]),
            recommended_sequence=sub_actions,
        ))
    dropped: list[DroppedStep] = []
    for d in raw.get("dropped_steps", []):
        dropped.append(DroppedStep(
            raw_emission=d.get("raw_emission", {}),
            reason=d["reason"],
            validator=d.get("validator", "fixture"),
            detail=d.get("detail"),
        ))
    return PlanResult(
        plan_id=raw.get("plan_id", "fixture-plan-id"),
        goal=raw.get("goal"),
        status=raw.get("status", "ok"),
        steps=steps,
        dropped_steps=dropped,
        total_monthly_impact_usd=float(
            raw.get("total_monthly_impact_usd",
                    sum(s.monthly_impact_usd for s in steps)),
        ),
        summary=raw.get("summary", ""),
        confidence=float(raw.get("confidence", 0.0)),
        prompt_template=raw.get("prompt_template", "savings_plan"),
        prompt_template_version=raw.get("prompt_template_version", "v2"),
        model=raw.get("model", "fixture"),
        provider=raw.get("provider", "fixture"),
        boundary_crossed=bool(raw.get("boundary_crossed", False)),
        parse_retry_count=int(raw.get("parse_retry_count", 0)),
        input_finding_ids=list(raw.get("input_finding_ids", [])),
        scan_id=raw.get("scan_id"),
        actor=raw.get("actor"),
        schema_version=raw.get("schema_version", CURRENT_SCHEMA_VERSION),
    )


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
    surface: str = SURFACE_PLANNER
    parse_retry_count: int = 0
    rerecorded: bool = False
    errors: list[str] = field(default_factory=list)
    # Populated only when surface == SURFACE_CONVERSATION. The runner
    # records it so the printer can show the surfaced text alongside
    # the rubric checks.
    conversation_outcome: "TurnOutcome | None" = None

    @property
    def ok(self) -> bool:
        """A fixture passes iff every GATE check passes. Warnings are
        recorded and printed but do not affect exit code — they exist
        to surface drift before we have enough confidence to gate on
        them. Promote via the _WARNING_TYPES map in rubric.py."""
        if self.errors:
            return False
        return all(c.ok for c in self.checks if c.level == LEVEL_GATE)

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if c.level == LEVEL_WARNING and not c.ok]


# ---------------------------------------------------------------------------
# Conversation fixture loading + execution
# ---------------------------------------------------------------------------

@dataclass
class ConversationFixture:
    name: str
    findings: list[Finding]
    plan: PlanResult
    question: str
    assertions: list[dict[str, Any]]
    recorded_responses: list[str]
    turn_history: list[dict[str, Any]]
    dir: Path

    @property
    def recording_path(self) -> Path:
        return self.dir / "recorded_response.json"


def _resolve_conversation_dir(scenario: str | Path) -> Path:
    p = Path(scenario)
    if p.is_absolute() or p.exists():
        return p
    return CONVERSATION_FIXTURES_DIR / scenario


def load_conversation_fixture(scenario: str | Path) -> ConversationFixture:
    fixture_dir = _resolve_conversation_dir(scenario)
    for required in ("findings.json", "plan.json", "question", "assertions.yaml"):
        if not (fixture_dir / required).exists():
            raise FileNotFoundError(f"{fixture_dir / required} missing")

    findings = _load_findings(fixture_dir / "findings.json")
    plan = _plan_from_dict(
        json.loads((fixture_dir / "plan.json").read_text(encoding="utf-8")),
    )
    question = (fixture_dir / "question").read_text(encoding="utf-8").strip()
    assertions = load_rubric(fixture_dir / "assertions.yaml")

    recording_path = fixture_dir / "recorded_response.json"
    if recording_path.exists():
        recorded = json.loads(recording_path.read_text(encoding="utf-8"))
        responses = recorded.get("responses", [])
    else:
        responses = []

    history_path = fixture_dir / "turn_history.json"
    if history_path.exists():
        turn_history = json.loads(history_path.read_text(encoding="utf-8"))
    else:
        turn_history = []

    return ConversationFixture(
        name=fixture_dir.name,
        findings=findings,
        plan=plan,
        question=question,
        assertions=assertions,
        recorded_responses=responses,
        turn_history=turn_history,
        dir=fixture_dir,
    )


def run_conversation_fixture(
    scenario: str | Path,
    *,
    re_record: bool = False,
    live_llm: LLMClient | None = None,
) -> EvalResult:
    """Run one conversation fixture. Builds a ThreadContext from the
    cached scan + plan, calls the plan-thread answerer with a replay
    LLM (or live with --re-record), applies the conversation rubric.

    The freshness gate is bypassed by pinning `now` to the context's
    `created_at` — fixtures are about validator behaviour, not the
    wall clock. A separate fixture path (out of scope for PR #9)
    would be needed to exercise aging/stale/expired tiers.
    """
    fixture = load_conversation_fixture(scenario)

    from datetime import datetime, timezone

    from analyzer.plan_conversation import answer_plan_thread_question
    from analyzer.thread_context import new_thread_context
    from config import WhisperConfig
    from presenters import ScanResult

    scan = ScanResult.from_findings(fixture.findings)
    context = new_thread_context(scan, plan_result=fixture.plan)
    # Inject any turn history from the fixture as already-validated
    # prose. The plan_conversation answerer doesn't re-validate it.
    from analyzer.thread_context import ConversationTurn
    for raw in fixture.turn_history:
        context.record_turn(ConversationTurn(
            user_question=raw["user_question"],
            assistant_answer=raw["assistant_answer"],
            cited_finding_ids=tuple(raw.get("cited_finding_ids", [])),
            turn_kind=raw.get("turn_kind", "answered"),
            created_at=datetime.now(timezone.utc),
        ))

    config = WhisperConfig()  # defaults — freshness thresholds active
    pinned_now = context.created_at  # fixtures freeze freshness to FRESH

    if re_record:
        if os.environ.get("WHISPER_ALLOW_REAL_LLM") != "1":
            return EvalResult(
                fixture=fixture.name,
                plan=fixture.plan,
                checks=[],
                surface=SURFACE_CONVERSATION,
                errors=[
                    "refusing to re-record without WHISPER_ALLOW_REAL_LLM=1. "
                    "This double-lock prevents accidental live-LLM calls."
                ],
            )
        if live_llm is None:
            from config import load_config
            from llm import make_llm_client
            live_llm = make_llm_client(
                load_config(), prompt_template="plan_thread_reply",
            )
        recorder = _RecordingLLM(live_llm)
        outcome = answer_plan_thread_question(
            fixture.question, context=context, client=recorder,
            config=config, now=pinned_now,
        )
        _write_conversation_recording(fixture.recording_path, recorder.recorded)
        return EvalResult(
            fixture=fixture.name,
            plan=fixture.plan,
            checks=run_conversation_rubric(
                fixture.assertions, outcome, scan=scan, plan=fixture.plan,
            ),
            surface=SURFACE_CONVERSATION,
            parse_retry_count=outcome.parse_retry_count,
            rerecorded=True,
            conversation_outcome=outcome,
        )

    # Replay path. Pre-routed questions don't call the LLM, so we only
    # need responses when the LLM is actually invoked. Provide a
    # placeholder to keep _ReplayLLM happy in pre-routed fixtures.
    responses = fixture.recorded_responses or ["{}"]
    replay = _ReplayLLM(responses)
    outcome = answer_plan_thread_question(
        fixture.question, context=context, client=replay,
        config=config, now=pinned_now,
    )
    return EvalResult(
        fixture=fixture.name,
        plan=fixture.plan,
        checks=run_conversation_rubric(
            fixture.assertions, outcome, scan=scan, plan=fixture.plan,
        ),
        surface=SURFACE_CONVERSATION,
        parse_retry_count=outcome.parse_retry_count,
        conversation_outcome=outcome,
    )


def _write_conversation_recording(path: Path, responses: list[str]) -> None:
    payload = {
        "responses": responses,
        "metadata": {
            "prompt_template": "plan_thread_reply",
            "prompt_template_version": "v1",
        },
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8")


def _all_conversation_scenarios() -> list[str]:
    if not CONVERSATION_FIXTURES_DIR.exists():
        return []
    return sorted(
        p.name for p in CONVERSATION_FIXTURES_DIR.iterdir() if p.is_dir()
    )


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
        description="Run SavingsPlanner / plan-thread-Q&A eval fixtures (record/replay).",
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
    parser.add_argument(
        "--surface", choices=ALL_SURFACES, default=SURFACE_PLANNER,
        help=(
            "Which fixture set to run. 'planner' (default) drives "
            "SavingsPlanner over fixtures in src/agent/evals/fixtures/. "
            "'conversation' drives the plan-thread answerer over "
            "fixtures in src/agent/evals/conversation_fixtures/. "
            "Conversation fixtures inherit the same replay/rerecord "
            "discipline as planner fixtures — they are NOT soft "
            "integration tests."
        ),
    )
    args = parser.parse_args(argv)

    if args.surface == SURFACE_CONVERSATION:
        scenarios = ([args.scenario] if args.scenario
                     else _all_conversation_scenarios())
        if not scenarios:
            print("no fixtures found in", CONVERSATION_FIXTURES_DIR, file=sys.stderr)
            return 1
        runner = run_conversation_fixture
    else:
        scenarios = [args.scenario] if args.scenario else _all_scenarios()
        if not scenarios:
            print("no fixtures found in", FIXTURES_DIR, file=sys.stderr)
            return 1
        runner = run_fixture

    overall_ok = True
    total_warnings = 0
    for scenario in scenarios:
        result = runner(scenario, re_record=args.re_record)
        _print_result(result)
        total_warnings += len(result.warnings)
        if not result.ok:
            overall_ok = False
    if total_warnings:
        print(f"\n{total_warnings} warning(s) across all fixtures "
              "(warnings do not affect exit code).")
    return 0 if overall_ok else 1


def _all_scenarios() -> list[str]:
    if not FIXTURES_DIR.exists():
        return []
    return sorted(p.name for p in FIXTURES_DIR.iterdir() if p.is_dir())


def _print_result(result: EvalResult) -> None:
    marker = "PASS" if result.ok else "FAIL"
    warn_count = len(result.warnings)
    suffix = f" ({warn_count} warning{'s' if warn_count != 1 else ''})" if warn_count else ""
    print(f"[{marker}{suffix}] {result.fixture}  ({result.surface})")
    if result.rerecorded:
        print("  (recording refreshed)")
    if result.surface == SURFACE_CONVERSATION and result.conversation_outcome is not None:
        outcome = result.conversation_outcome
        fb = outcome.fallback.value if outcome.fallback else "none"
        print(f"  tier={outcome.freshness_tier.value} fallback={fb}")
        # Truncated surfaced-text preview so the operator can eyeball
        # what was rendered without scrolling.
        preview = outcome.surfaced_text.replace("\n", " ")[:120]
        print(f"  surfaced: {preview!r}")
    for c in result.checks:
        if c.level == LEVEL_WARNING:
            line_marker = "  ⚠" if not c.ok else "  ✓"
        else:
            line_marker = "  ✓" if c.ok else "  ✗"
        label = f"{c.assertion_type}"
        if c.level == LEVEL_WARNING:
            label = f"[warn] {label}"
        print(f"{line_marker} {label}: {c.detail}")
    for err in result.errors:
        print(f"  ! {err}")


if __name__ == "__main__":
    sys.exit(main())
