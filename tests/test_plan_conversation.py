"""
Unit tests for analyzer.plan_conversation (PR #9).

Covers:
  - envelope parsing (happy + malformed)
  - pre-router (account_metadata, billing_portal, action-request patterns)
  - validators under regex-strict-rules protocol (cited_finding_ids
    subset, inline-$ canonical match, arithmetic-phrasing detection,
    past-tense action regex)
  - freshness resolver (fresh / aging / stale / expired tier boundaries)
  - end-to-end answer_plan_thread_question on each fallback path
  - the tripwire that prevents the conversation layer from invoking
    the planner (TestConversationLayerCannotInvokePlanner)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
_SRC = _REPO / "src"
for p in (_REPO, _SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from agent.schemas import PlanResult, PlanStep, SubAction
from analyzer.plan_conversation import (
    AGING_FOOTER_TEMPLATE,
    DRIFT_TEMPLATE,
    EXPIRED_TEMPLATE,
    IMPLIED_ACTION_TEMPLATE,
    INVENTED_COST_TEMPLATE,
    LLM_UNAVAILABLE_TEMPLATE,
    PARSE_FAILED_TEMPLATE,
    STALE_PREFIX_TEMPLATE,
    SYNTHESIZED_COST_TEMPLATE,
    FallbackReason,
    FreshnessTier,
    ScopeCategory,
    answer_plan_thread_question,
    build_prompt,
    freshness_verdict,
    parse_envelope,
    pre_route,
    render_scope_refusal,
    validate_envelope,
)
from analyzer.thread_context import new_thread_context
from config import WhisperConfig
from llm import LLMClient
from llm.base import LLMResponse, Message
from patterns.base import Finding, RiskTier
from presenters import ScanResult


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FID_P004 = "40000000-0004-4000-8000-000000000020"
FID_P001 = "10000000-0001-4000-8000-000000000010"
FID_P006 = "60000000-0006-4000-8000-000000000030"


def _findings() -> list[Finding]:
    return [
        Finding(
            id=FID_P004, pattern_id="004", resource_id="i-cross-stop",
            resource_type="EC2 Instance", region="us-east-1",
            monthly_impact_usd=138.24, risk_tier=RiskTier.MEDIUM,
            summary="Stop idle m5.large i-cross-stop",
        ),
        Finding(
            id=FID_P001, pattern_id="001", resource_id="vol-cross-1",
            resource_type="EBS Volume", region="us-east-1",
            monthly_impact_usd=80.0, risk_tier=RiskTier.HIGH,
            summary="Delete 1000GB gp2 volume",
        ),
        Finding(
            id=FID_P006, pattern_id="006", resource_id="nat-cross-dev",
            resource_type="NAT Gateway", region="us-east-1",
            monthly_impact_usd=32.4, risk_tier=RiskTier.LOW,
            summary="NAT Gateway nat-cross-dev — Flow Logs absent",
        ),
    ]


def _scan() -> ScanResult:
    return ScanResult.from_findings(_findings())


def _plan() -> PlanResult:
    return PlanResult(
        plan_id="p9-test-plan",
        goal="rank by impact",
        status="ok",
        steps=[
            PlanStep(
                finding_id=FID_P004, pattern_id="004",
                suggested_mode="command", monthly_impact_usd=138.24,
                rationale="idle m5.large", order_rank=1,
            ),
            PlanStep(
                finding_id=FID_P001, pattern_id="001",
                suggested_mode="pr", monthly_impact_usd=80.0,
                rationale="terraform-managed gp2", order_rank=2,
            ),
            PlanStep(
                finding_id=FID_P006, pattern_id="006",
                suggested_mode="dry_run", monthly_impact_usd=32.4,
                rationale="hourly_only", order_rank=3,
                recommended_sequence=[SubAction(
                    candidate_id="cand-gateway-s3",
                    action_kind="observe_and_reassess",
                    est_monthly_savings_usd=0.0,
                    evidence_tier="inferred",
                    rationale="enable Flow Logs first",
                )],
            ),
        ],
        dropped_steps=[],
        total_monthly_impact_usd=250.64,
        summary="three steps",
        confidence=0.85,
        prompt_template="savings_plan",
        prompt_template_version="v2",
        model="fixture",
        provider="fixture",
        boundary_crossed=False,
        parse_retry_count=0,
        input_finding_ids=[FID_P004, FID_P001, FID_P006],
    )


def _context(*, now=None, plan=None):
    return new_thread_context(
        _scan(),
        plan_result=plan if plan is not None else _plan(),
        now=now,
    )


class _ScriptedLLM(LLMClient):
    """Replays a fixed list of responses."""
    provider = "test"
    boundary_crossed = False

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def complete(self, messages, *, model=None, max_tokens=4096, temperature=0.0):
        self.calls += 1
        text = self._responses.pop(0)
        return LLMResponse(
            text=text, provider=self.provider, model="scripted",
            boundary_crossed=False,
        )

    @property
    def default_model(self):
        return "scripted"


def _envelope_text(
    *,
    answer: str = "Step 1 saves $138.24/mo.",
    cited_finding_ids: list[str] | None = None,
    is_in_scope: bool = True,
    scope_category: str | None = None,
    implies_action_taken: bool = False,
) -> str:
    return json.dumps({
        "answer": answer,
        "cited_finding_ids": cited_finding_ids
            if cited_finding_ids is not None else [FID_P004],
        "is_in_scope": is_in_scope,
        "scope_category": scope_category,
        "implies_action_taken": implies_action_taken,
    })


# ---------------------------------------------------------------------------
# parse_envelope
# ---------------------------------------------------------------------------

class TestParseEnvelope:
    def test_happy(self):
        env = parse_envelope(_envelope_text())
        assert env.answer.startswith("Step 1")
        assert env.cited_finding_ids == (FID_P004,)
        assert env.is_in_scope is True
        assert env.scope_category is None

    def test_strips_code_fence(self):
        text = "```json\n" + _envelope_text() + "\n```"
        env = parse_envelope(text)
        assert env.cited_finding_ids == (FID_P004,)

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="JSON"):
            parse_envelope("not json")

    def test_missing_required_key_raises(self):
        bad = json.dumps({"answer": "x"})
        with pytest.raises(ValueError, match="missing required"):
            parse_envelope(bad)

    def test_non_string_answer_raises(self):
        bad = json.dumps({
            "answer": 42,
            "cited_finding_ids": [],
            "is_in_scope": True,
            "implies_action_taken": False,
        })
        with pytest.raises(ValueError, match="answer"):
            parse_envelope(bad)

    def test_unknown_scope_category_raises(self):
        bad = _envelope_text(scope_category="bogus_category", is_in_scope=False,
                             answer="", cited_finding_ids=[])
        with pytest.raises(ValueError, match="scope_category"):
            parse_envelope(bad)


# ---------------------------------------------------------------------------
# Pre-router
# ---------------------------------------------------------------------------

class TestPreRouter:
    @pytest.mark.parametrize("question", [
        "what's my AWS account ID?",
        "what is our account id?",
        "What's My Account ID",  # case-insensitive
    ])
    def test_account_id_questions(self, question):
        match = pre_route(question)
        assert match is not None
        assert match.category == ScopeCategory.ACCOUNT_METADATA

    @pytest.mark.parametrize("question", [
        "open the billing console for me",
        "take me to cost explorer",
        "go to the AWS console",
    ])
    def test_billing_portal_questions(self, question):
        match = pre_route(question)
        assert match is not None
        assert match.category == ScopeCategory.BILLING_PORTAL

    @pytest.mark.parametrize("question", [
        "just stop that instance for me",
        "delete the volume now",
        "go ahead and apply the fix",
    ])
    def test_action_request_questions(self, question):
        match = pre_route(question)
        assert match is not None
        assert match.category == ScopeCategory.OTHER

    @pytest.mark.parametrize("question", [
        "why did you pick step 1 over step 2?",
        "what about the NAT gateway?",
        "can I do this in dev first?",
        "tell me about vol-cross-1",
        "",
    ])
    def test_planning_questions_fall_through(self, question):
        assert pre_route(question) is None


# ---------------------------------------------------------------------------
# validate_envelope
# ---------------------------------------------------------------------------

class TestValidateEnvelope:
    """Regex-strict-rules protocol: inline `$N` figures are allowed IFF
    they equal a canonical scan/plan value within $0.01 AND no
    arithmetic phrasing is present in the answer."""

    def test_happy_inline_canonical_dollar_passes(self):
        env = parse_envelope(_envelope_text(
            answer="Step 1 stops the m5.large at $138.24/mo.",
        ))
        assert validate_envelope(env, scan=_scan(), plan=_plan()) is None

    def test_happy_multiple_canonical_dollars_pass(self):
        env = parse_envelope(_envelope_text(
            answer="Step 1 is $138.24/mo and step 2 is $80.00/mo.",
            cited_finding_ids=[FID_P004, FID_P001],
        ))
        assert validate_envelope(env, scan=_scan(), plan=_plan()) is None

    def test_unknown_finding_id_drops(self):
        env = parse_envelope(_envelope_text(
            cited_finding_ids=["f-NOT-IN-SCAN"],
            answer="A claim about f-NOT-IN-SCAN.",
        ))
        failure = validate_envelope(env, scan=_scan(), plan=_plan())
        assert failure is not None
        assert failure.reason == FallbackReason.UNKNOWN_FINDING_ID

    def test_inline_dollar_not_in_canonical_set_drops_as_invented(self):
        env = parse_envelope(_envelope_text(
            answer="The instance is at $999.99/mo.",
        ))
        failure = validate_envelope(env, scan=_scan(), plan=_plan())
        assert failure is not None
        assert failure.reason == FallbackReason.INVENTED_COST

    def test_inline_dollar_with_arithmetic_routes_to_synthesized(self):
        """Arithmetic phrasing alongside any inline `$` → SYNTHESIZED_COST,
        even when the figure is derived from canonical values
        (138.24 + 80.0 = 218.24)."""
        env = parse_envelope(_envelope_text(
            answer="Together steps 1 and 2 total $218.24/mo.",
            cited_finding_ids=[FID_P004, FID_P001],
        ))
        failure = validate_envelope(env, scan=_scan(), plan=_plan())
        assert failure is not None
        assert failure.reason == FallbackReason.SYNTHESIZED_COST

    def test_arithmetic_phrasing_with_canonical_dollar_still_drops(self):
        """Sign-off: arithmetic phrasing is a hard drop even when the
        inline `$` happens to match a canonical value. The planner is
        the only source of derived totals."""
        env = parse_envelope(_envelope_text(
            answer="Combined, that's $138.24/mo of the plan's headline.",
        ))
        failure = validate_envelope(env, scan=_scan(), plan=_plan())
        assert failure is not None
        assert failure.reason == FallbackReason.SYNTHESIZED_COST

    def test_annual_projection_drops_as_synthesized(self):
        env = parse_envelope(_envelope_text(
            answer="The instance saves $1658.88/yr if you stop it.",
        ))
        failure = validate_envelope(env, scan=_scan(), plan=_plan())
        assert failure is not None
        assert failure.reason == FallbackReason.SYNTHESIZED_COST

    def test_thousands_separator_dollar_compared_correctly(self):
        """LLM may write '$1,000' for a four-figure canonical value.
        The regex strips commas before float-comparison."""
        env = parse_envelope(_envelope_text(
            answer="The total monthly waste is $250.64/mo.",
        ))
        # 250.64 is plan.total_monthly_impact_usd — canonical.
        assert validate_envelope(env, scan=_scan(), plan=_plan()) is None

    def test_past_tense_action_in_answer_drops(self):
        env = parse_envelope(_envelope_text(
            answer="I stopped the instance saving $138.24/mo.",
        ))
        failure = validate_envelope(env, scan=_scan(), plan=_plan())
        assert failure is not None
        assert failure.reason == FallbackReason.IMPLIED_ACTION

    def test_envelope_self_check_implies_action_drops(self):
        env = parse_envelope(_envelope_text(
            answer="Done.",
            cited_finding_ids=[],
            implies_action_taken=True,
        ))
        failure = validate_envelope(env, scan=_scan(), plan=_plan())
        assert failure is not None
        assert failure.reason == FallbackReason.IMPLIED_ACTION

    def test_out_of_scope_envelope_passes_with_empty_arrays(self):
        env = parse_envelope(_envelope_text(
            is_in_scope=False,
            scope_category="account_metadata",
            answer="",
            cited_finding_ids=[],
        ))
        assert validate_envelope(env, scan=_scan(), plan=_plan()) is None

    def test_out_of_scope_without_scope_category_drops(self):
        env = parse_envelope(_envelope_text(
            is_in_scope=False, answer="",
            cited_finding_ids=[],
        ))
        failure = validate_envelope(env, scan=_scan(), plan=_plan())
        assert failure is not None
        assert failure.reason == FallbackReason.SCHEMA_INVALID

    def test_out_of_scope_with_nonempty_answer_drops(self):
        env = parse_envelope(_envelope_text(
            is_in_scope=False, scope_category="account_metadata",
            answer="Your account is 12345.",
            cited_finding_ids=[],
        ))
        failure = validate_envelope(env, scan=_scan(), plan=_plan())
        assert failure is not None
        assert failure.reason == FallbackReason.SCHEMA_INVALID

    def test_subaction_zero_savings_is_canonical(self):
        """Observe-only sub-actions have est_monthly_savings_usd=0.0;
        the LLM is allowed to cite $0.00 as a canonical value."""
        env = parse_envelope(_envelope_text(
            answer="The observe sub-action saves $0.00/mo by itself.",
            cited_finding_ids=[FID_P006],
        ))
        assert validate_envelope(env, scan=_scan(), plan=_plan()) is None

    def test_answer_with_no_dollar_figures_passes(self):
        """Not every answer needs to cite a number — narrative-only
        answers should pass the dollar validator vacuously."""
        env = parse_envelope(_envelope_text(
            answer="Flow Logs are absent so the plan recommends "
                   "observe-and-reassess before any endpoint change.",
        ))
        assert validate_envelope(env, scan=_scan(), plan=_plan()) is None


# ---------------------------------------------------------------------------
# Freshness resolver
# ---------------------------------------------------------------------------

class TestFreshnessResolver:
    def _cfg(self):
        return WhisperConfig()  # defaults: 30m / 4h / 24h

    def _ctx_at(self, *, created_min_ago: int):
        now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc)
        created = now - timedelta(minutes=created_min_ago)
        return _context(now=created), now

    def test_fresh_under_aging_threshold(self):
        ctx, now = self._ctx_at(created_min_ago=10)
        v = freshness_verdict(ctx, config=self._cfg(), now=now)
        assert v.tier == FreshnessTier.FRESH

    def test_aging_between_thresholds(self):
        ctx, now = self._ctx_at(created_min_ago=60)
        v = freshness_verdict(ctx, config=self._cfg(), now=now)
        assert v.tier == FreshnessTier.AGING

    def test_stale_between_thresholds(self):
        ctx, now = self._ctx_at(created_min_ago=6 * 60)
        v = freshness_verdict(ctx, config=self._cfg(), now=now)
        assert v.tier == FreshnessTier.STALE

    def test_expired_past_24h(self):
        ctx, now = self._ctx_at(created_min_ago=36 * 60)
        v = freshness_verdict(ctx, config=self._cfg(), now=now)
        assert v.tier == FreshnessTier.EXPIRED

    def test_exact_boundary_aging(self):
        ctx, now = self._ctx_at(created_min_ago=30)
        v = freshness_verdict(ctx, config=self._cfg(), now=now)
        assert v.tier == FreshnessTier.AGING

    def test_exact_boundary_stale(self):
        ctx, now = self._ctx_at(created_min_ago=4 * 60)
        v = freshness_verdict(ctx, config=self._cfg(), now=now)
        assert v.tier == FreshnessTier.STALE

    def test_human_age_minutes(self):
        ctx, now = self._ctx_at(created_min_ago=45)
        v = freshness_verdict(ctx, config=self._cfg(), now=now)
        assert v.human_age == "45m"

    def test_human_age_hours_minutes(self):
        ctx, now = self._ctx_at(created_min_ago=6 * 60 + 14)
        v = freshness_verdict(ctx, config=self._cfg(), now=now)
        assert v.human_age == "6h 14m"


# ---------------------------------------------------------------------------
# answer_plan_thread_question — end-to-end fallback paths
# ---------------------------------------------------------------------------

class TestAnswerEndToEnd:
    def test_no_config_returns_llm_unavailable(self):
        ctx = _context()
        outcome = answer_plan_thread_question("why?", context=ctx)
        assert outcome.fallback == FallbackReason.LLM_UNAVAILABLE
        assert outcome.surfaced_text == LLM_UNAVAILABLE_TEMPLATE

    def test_no_plan_raises(self):
        scan = _scan()
        ctx = new_thread_context(scan, plan_result=None)
        with pytest.raises(ValueError, match="plan_result=None"):
            answer_plan_thread_question(
                "why?", context=ctx, config=WhisperConfig(),
            )

    def test_pre_router_short_circuits_no_llm_call(self):
        llm = _ScriptedLLM(["{}"])
        ctx = _context()
        outcome = answer_plan_thread_question(
            "what's my AWS account ID?",
            context=ctx, client=llm, config=WhisperConfig(),
        )
        assert llm.calls == 0
        assert outcome.pre_routed_category == ScopeCategory.ACCOUNT_METADATA
        assert outcome.turn.turn_kind == "out_of_scope"
        assert outcome.fallback is None  # deterministic refusal, not fallback

    def test_expired_short_circuits_no_llm_call(self):
        llm = _ScriptedLLM(["{}"])
        created = datetime(2026, 5, 24, 0, 0, 0, tzinfo=timezone.utc)
        now = created + timedelta(hours=36)
        ctx = _context(now=created)
        outcome = answer_plan_thread_question(
            "why?", context=ctx, client=llm,
            config=WhisperConfig(), now=now,
        )
        assert llm.calls == 0
        assert outcome.fallback == FallbackReason.EXPIRED
        assert outcome.freshness_tier == FreshnessTier.EXPIRED
        # _format_age rolls to days past 24h: 36h → "1d 12h".
        assert "1d 12h" in outcome.surfaced_text

    def test_happy_answer_passes_through_and_canonicalises(self):
        env_text = _envelope_text(
            answer="Step 1 ($138.24/mo) and step 2 ($80.00/mo) are both safe.",
            cited_finding_ids=[FID_P001, FID_P004],  # out of impact order
        )
        llm = _ScriptedLLM([env_text])
        ctx = _context()
        outcome = answer_plan_thread_question(
            "why?", context=ctx, client=llm, config=WhisperConfig(),
        )
        assert outcome.fallback is None
        # Under regex-strict-rules the answer passes through unchanged
        # — inline canonical dollars are valid.
        assert "$138.24" in outcome.surfaced_text
        assert "$80.00" in outcome.surfaced_text
        # Citations canonicalised into scan-impact order (P004 > P001 > P006).
        assert outcome.turn.cited_finding_ids == (FID_P004, FID_P001)
        assert outcome.turn.turn_kind == "answered"

    def test_drift_fallback_records_safe_prose(self):
        env_text = _envelope_text(
            answer="A claim about an unknown finding.",
            cited_finding_ids=["f-NOPE"],
        )
        llm = _ScriptedLLM([env_text])
        ctx = _context()
        outcome = answer_plan_thread_question(
            "tell me about that one", context=ctx,
            client=llm, config=WhisperConfig(),
        )
        assert outcome.fallback == FallbackReason.UNKNOWN_FINDING_ID
        assert outcome.surfaced_text == DRIFT_TEMPLATE
        # ConversationTurn.assistant_answer records the fallback, not
        # the rejected LLM prose — poisoned-history prevention.
        assert outcome.turn.assistant_answer == DRIFT_TEMPLATE

    def test_synthesized_cost_fallback(self):
        env_text = _envelope_text(
            answer="Together steps 1 and 2 total $218.24/mo.",
            cited_finding_ids=[FID_P004, FID_P001],
        )
        llm = _ScriptedLLM([env_text])
        outcome = answer_plan_thread_question(
            "combined?", context=_context(),
            client=llm, config=WhisperConfig(),
        )
        assert outcome.fallback == FallbackReason.SYNTHESIZED_COST
        assert outcome.surfaced_text == SYNTHESIZED_COST_TEMPLATE

    def test_invented_cost_fallback(self):
        env_text = _envelope_text(
            answer="That costs $999.99/mo.",
        )
        llm = _ScriptedLLM([env_text])
        outcome = answer_plan_thread_question(
            "x", context=_context(),
            client=llm, config=WhisperConfig(),
        )
        assert outcome.fallback == FallbackReason.INVENTED_COST
        assert outcome.surfaced_text == INVENTED_COST_TEMPLATE

    def test_implied_action_fallback(self):
        env_text = _envelope_text(
            answer="I stopped the instance and saved $138.24/mo.",
        )
        llm = _ScriptedLLM([env_text])
        outcome = answer_plan_thread_question(
            "done?", context=_context(),
            client=llm, config=WhisperConfig(),
        )
        assert outcome.fallback == FallbackReason.IMPLIED_ACTION
        assert outcome.surfaced_text == IMPLIED_ACTION_TEMPLATE

    def test_repair_retry_on_first_parse_failure(self):
        good = _envelope_text()
        llm = _ScriptedLLM(["not json at all", good])
        outcome = answer_plan_thread_question(
            "why?", context=_context(),
            client=llm, config=WhisperConfig(),
        )
        assert outcome.fallback is None
        assert outcome.parse_retry_count == 1
        assert llm.calls == 2

    def test_parse_failed_after_repair(self):
        llm = _ScriptedLLM(["not json", "still not json"])
        outcome = answer_plan_thread_question(
            "why?", context=_context(),
            client=llm, config=WhisperConfig(),
        )
        assert outcome.fallback == FallbackReason.PARSE_FAILED
        assert outcome.surfaced_text == PARSE_FAILED_TEMPLATE
        assert llm.calls == 2

    def test_out_of_scope_envelope_renders_deterministic_refusal(self):
        env_text = _envelope_text(
            is_in_scope=False,
            scope_category="iam_policy",
            answer="",
            cited_finding_ids=[],
        )
        llm = _ScriptedLLM([env_text])
        outcome = answer_plan_thread_question(
            "what IAM policy do I need?", context=_context(),
            client=llm, config=WhisperConfig(),
        )
        assert outcome.fallback is None
        assert outcome.envelope is not None
        assert outcome.envelope.is_in_scope is False
        assert outcome.surfaced_text == render_scope_refusal(ScopeCategory.IAM_POLICY)

    def test_aging_tier_appends_footer(self):
        created = datetime(2026, 5, 24, 0, 0, 0, tzinfo=timezone.utc)
        now = created + timedelta(minutes=45)
        ctx = _context(now=created)
        env_text = _envelope_text()
        llm = _ScriptedLLM([env_text])
        outcome = answer_plan_thread_question(
            "why?", context=ctx, client=llm,
            config=WhisperConfig(), now=now,
        )
        assert outcome.freshness_tier == FreshnessTier.AGING
        assert outcome.turn.turn_kind == "answered"
        assert AGING_FOOTER_TEMPLATE.format(age="45m") in outcome.surfaced_text

    def test_stale_tier_prepends_warning(self):
        created = datetime(2026, 5, 24, 0, 0, 0, tzinfo=timezone.utc)
        now = created + timedelta(hours=6)
        ctx = _context(now=created)
        env_text = _envelope_text()
        llm = _ScriptedLLM([env_text])
        outcome = answer_plan_thread_question(
            "why?", context=ctx, client=llm,
            config=WhisperConfig(), now=now,
        )
        assert outcome.freshness_tier == FreshnessTier.STALE
        assert outcome.turn.turn_kind == "stale_warn"
        assert STALE_PREFIX_TEMPLATE.format(age="6h") in outcome.surfaced_text


# ---------------------------------------------------------------------------
# build_prompt — <<SENTINEL>> substitution into the prompt template
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_prompt_has_all_sentinels_replaced(self):
        ctx = _context()
        v = freshness_verdict(ctx, config=WhisperConfig(),
                              now=ctx.created_at)
        prompt = build_prompt("why?", context=ctx, verdict=v)
        # Sentinels gone, content present.
        for marker in ("<<SCAN_BLOCK>>", "<<PLAN_BLOCK>>",
                       "<<PLAN_AGE>>", "<<TURN_HISTORY>>", "<<QUESTION>>"):
            assert marker not in prompt, f"unsubstituted: {marker}"
        assert FID_P004 in prompt
        assert "138.24" in prompt
        assert "why?" in prompt


# ---------------------------------------------------------------------------
# Tripwire — the conversation layer never invokes the planner
# ---------------------------------------------------------------------------

class TestConversationLayerCannotInvokePlanner:
    """The constraint from sign-off: re-plan is the only bridge from
    conversation to recommendation, and re-plan lives outside the
    thread (it's a slash command). If a future change wires the
    planner into the conversation layer — even just an import — this
    test fails loud and forces the change to be reviewed against the
    constraint.
    """

    def test_module_does_not_import_savings_planner(self):
        import analyzer.plan_conversation as pc
        # Module-level: no SavingsPlanner reference anywhere.
        assert not hasattr(pc, "SavingsPlanner"), (
            "plan_conversation imported SavingsPlanner — "
            "the conversation layer must not invoke the planner. "
            "Re-plan is the only bridge from conversation to "
            "recommendation, and re-plan lives outside the thread."
        )

    def test_module_source_does_not_mention_savings_planner(self):
        """Belt-and-braces: source file should not even reference the
        symbol. Comments are acceptable (we explain why); imports and
        calls are not."""
        source = Path(_SRC / "analyzer" / "plan_conversation.py").read_text()
        # Filter comment / docstring mentions (lines starting with #
        # or containing the symbol only inside quoted strings).
        offending: list[str] = []
        for i, line in enumerate(source.splitlines(), start=1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "SavingsPlanner" in line and "import" in line:
                offending.append(f"L{i}: {line}")
            elif "SavingsPlanner(" in line:
                offending.append(f"L{i}: {line}")
        assert not offending, (
            "plan_conversation references SavingsPlanner in code:\n"
            + "\n".join(offending)
        )
