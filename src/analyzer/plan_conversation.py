"""
Plan-thread Q&A — answer follow-up questions against a cached
(ScanResult, PlanResult) pair.

The conversation layer's contract (sign-off, PR #9):

  - It MAY explain, filter, compare, contextualise existing findings
    and plan steps.
  - It MUST NOT re-rank, invent IDs/costs/modes, imply an action was
    taken, or imply a re-plan happened.
  - It NEVER invokes `SavingsPlanner`. The only bridge from
    conversation to recommendation is the explicit `/whisper plan`
    slash command in a channel — which produces a fresh thread.
  - The tripwire test `TestConversationLayerCannotInvokePlanner`
    enforces the no-import rule.

Pipeline:

    question + ThreadContext
        │
        ▼  freshness gate (no LLM call if expired)
        │
        ▼  deterministic pre-router (account_metadata / billing /
        │  action requests are answered without an LLM call)
        │
        ▼  prompt build (scan + plan + bounded turn ring + age)
        │
        ▼  LLM.complete  (one repair retry on parse failure)
        │
        ▼  envelope parse  →  validators chain  →  surfaced answer
        │                                          or typed fallback
        ▼
    surfaced prose  +  recorded ConversationTurn

Dollar protocol: strict regex (sign-off, PR #9 — the
placeholder-protocol alternative was deferred for lack of a live-LLM
trial). Inline `$N(.NN)?` figures in the answer are allowed IFF they
match a canonical scan/plan value within $0.01. Arithmetic phrasing
(`together`, `total`, `%`, `per year`, etc.) in the same answer is a
hard drop as `SYNTHESIZED_COST`, even when the figure coincidentally
matches a canonical value — the planner is the only source of derived
totals. See `agentic/plan_thread_qa_agentic.md` for the decision
record and the placeholder fallback path if real-LLM behaviour later
makes regex too noisy.

Drop discipline mirrors the planner's: a closed set of fallback
reasons, structured records of why a turn ended where it did, the
user only ever sees validated prose or a deterministic fallback
constant. Eval rubric asserts against the fallback reasons exactly
like `dropped_reason_present` asserts against `DropReason`.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

_SRC = Path(__file__).parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from llm import LLMClient, Message, make_llm_client  # noqa: E402
from prompts import load_template  # noqa: E402
from prompts.plan_thread_reply import REPAIR_INSTRUCTION  # noqa: E402

if TYPE_CHECKING:
    from agent.schemas import PlanResult
    from config import WhisperConfig
    from presenters import ScanResult

from analyzer.thread_context import ConversationTurn, ThreadContext  # noqa: E402


logger = logging.getLogger(__name__)
TEMPLATE_NAME = "plan_thread_reply"


# Cap for how many findings / plan steps we serialise into the prompt
# context. Mirrors `analyzer.conversation.MAX_FINDINGS_IN_CONTEXT`.
MAX_FINDINGS_IN_CONTEXT = 10
MAX_PLAN_STEPS_IN_CONTEXT = 15

# Tolerance for the LLM-cited dollar value vs canonical sources. One
# cent — same as the planner's validator (MONTHLY_IMPACT_TOLERANCE).
DOLLAR_TOLERANCE = 0.01


# ---------------------------------------------------------------------------
# Closed enums — the planner's discipline applied to conversation
# ---------------------------------------------------------------------------

class FreshnessTier(str, Enum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    EXPIRED = "expired"


class ScopeCategory(str, Enum):
    """Closed set the LLM is allowed to emit for `is_in_scope=false`.

    Mirrors the planner's `DropReason` closed-set discipline:
    out-of-scope is not free text; it's one of these four categories
    and the renderer keys the deterministic refusal off the category.
    Adding a category is a product decision (see stop-and-surface
    rules in agentic/plan_thread_qa_agentic.md).
    """
    ACCOUNT_METADATA = "account_metadata"
    BILLING_PORTAL = "billing_portal"
    IAM_POLICY = "iam_policy"
    OTHER = "other"


class FallbackReason(str, Enum):
    """Why a turn ended in a deterministic fallback rather than a
    validated LLM answer. Eval rubric asserts against these the same
    way it asserts against `DropReason`."""
    UNKNOWN_FINDING_ID = "unknown_finding_id"
    INVENTED_COST = "invented_cost"          # cited amount not in scan/plan
    SYNTHESIZED_COST = "synthesized_cost"    # arithmetic over canonical figures (sum, %)
    IMPLIED_ACTION = "implied_action"        # past-tense execution language
    PARSE_FAILED = "parse_failed"
    SCHEMA_INVALID = "schema_invalid"
    EXPIRED = "expired"
    LLM_UNAVAILABLE = "llm_unavailable"


# Past-tense action verbs that imply the assistant executed something.
# Recommendation language ("you could stop", "the plan recommends") is
# fine and stays out of this list — the action-verb validator is a
# hard drop only for past-tense execution implication, per sign-off.
PAST_TENSE_ACTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"\b{p}\b", re.IGNORECASE) for p in (
        r"i stopped", r"i deleted", r"i removed", r"i terminated",
        r"i opened a pr", r"i merged", r"i executed", r"i ran",
        r"i applied", r"i fixed", r"i remediated",
    )
)

# `$N(.NN)?` matcher. Intentionally narrow — matches the formats the
# LLM actually produces (5.50, 5, 5,500). Used by `validate_envelope`
# to enforce the regex-strict-rules protocol: every inline `$` figure
# must equal a canonical scan/plan value within $0.01.
INLINE_DOLLAR_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")


# ---------------------------------------------------------------------------
# Deterministic pre-router — runs BEFORE any LLM call
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PreRouterMatch:
    category: ScopeCategory
    message: str


# Patterns chosen for high precision over recall. False positives cost
# more than false negatives here (a legitimate question routed to a
# refusal is bad; an out-of-scope question routed to the LLM still
# gets caught by the envelope's is_in_scope=false path). Per the
# stop-and-surface rules, broadening these patterns requires a
# product decision, not silent tuning.
_PRE_ROUTER_RULES: tuple[tuple[re.Pattern[str], ScopeCategory], ...] = (
    # account metadata
    (re.compile(r"\bwhat(?:'s| is)\s+(?:my|our)?\s*(aws\s+)?account\s+id\b", re.IGNORECASE),
     ScopeCategory.ACCOUNT_METADATA),
    (re.compile(r"\b(account|organisation|organization)\s+(id|number)\b", re.IGNORECASE),
     ScopeCategory.ACCOUNT_METADATA),
    # billing portal
    (re.compile(r"\bopen\b.+\b(billing|cost\s+explorer|aws\s+console)\b", re.IGNORECASE),
     ScopeCategory.BILLING_PORTAL),
    (re.compile(r"\b(go to|take me to)\b.+\b(billing|cost\s+explorer|console)\b", re.IGNORECASE),
     ScopeCategory.BILLING_PORTAL),
    # direct action requests targeting AWS resources from the thread
    (re.compile(r"\b(just\s+)?(stop|delete|terminate|remove|kill)\b.+\b(for me|now|please)\b",
                re.IGNORECASE),
     ScopeCategory.OTHER),
    (re.compile(r"\b(go ahead|just do it|apply (?:it|the fix)|run the fix)\b",
                re.IGNORECASE),
     ScopeCategory.OTHER),
)


def pre_route(question: str) -> PreRouterMatch | None:
    """Return a deterministic refusal match, or None to let the LLM answer.

    Catches obvious out-of-scope and action-request phrasings without
    spending tokens on them. Designed for high precision: when in
    doubt, fall through to the LLM (which will set is_in_scope=false
    on its own if the question really is out of scope).
    """
    text = question.strip()
    if not text:
        return None
    for pattern, category in _PRE_ROUTER_RULES:
        if pattern.search(text):
            return PreRouterMatch(
                category=category,
                message=render_scope_refusal(category),
            )
    return None


# ---------------------------------------------------------------------------
# Envelope parsing + validation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Envelope:
    """Canonical, post-parse view of the LLM's response. Built only
    after JSON parsing succeeds — fields that fail downstream
    validation become fallback reasons, not malformed envelopes.

    No `cited_dollar_amounts` field under the regex-strict-rules
    protocol: dollar figures appear inline in `answer` and are
    validated against the canonical scan/plan universe by regex
    after parsing. See module docstring for the decision record.
    """
    answer: str
    cited_finding_ids: tuple[str, ...]
    is_in_scope: bool
    scope_category: ScopeCategory | None
    implies_action_taken: bool


@dataclass
class TurnOutcome:
    """Result of one full answer cycle, mirrors planner.PlanResult shape.

    `surfaced_text` is what the Slack/CLI surface posts. `turn` is the
    record stored in the thread's bounded ring. `fallback` is None on
    successful answers; otherwise the reason the LLM's response was
    rejected (or the path the framework took instead, e.g. expired).
    """
    surfaced_text: str
    turn: ConversationTurn
    fallback: FallbackReason | None
    freshness_tier: FreshnessTier
    # Debug-only: the raw envelope if one was parsed. Tests inspect it;
    # surfaces don't.
    envelope: Envelope | None = None
    parse_retry_count: int = 0
    # Pre-router category if the question was routed deterministically.
    pre_routed_category: ScopeCategory | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


_REQUIRED_KEYS: tuple[str, ...] = (
    "answer", "cited_finding_ids", "is_in_scope", "implies_action_taken",
)


def parse_envelope(raw_text: str) -> Envelope:
    """Parse the LLM's JSON envelope. Raises `ValueError` on schema
    failures so the caller can map to the right FallbackReason."""
    text = (raw_text or "").strip()
    # Strip an optional ```json ... ``` fence in case the model ignored
    # the "no fences" rule. We don't try to repair more aggressively;
    # the repair-retry instruction handles other malformed cases.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"envelope did not parse as JSON: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"envelope is not an object: {type(data).__name__}")

    missing = [k for k in _REQUIRED_KEYS if k not in data]
    if missing:
        raise ValueError(f"envelope missing required keys: {missing}")

    answer = data["answer"]
    if not isinstance(answer, str):
        raise ValueError("`answer` must be a string")

    cited_ids = data["cited_finding_ids"]
    if not isinstance(cited_ids, list) or not all(isinstance(x, str) for x in cited_ids):
        raise ValueError("`cited_finding_ids` must be a list of strings")

    is_in_scope = data["is_in_scope"]
    if not isinstance(is_in_scope, bool):
        raise ValueError("`is_in_scope` must be a boolean")

    implies_action = data["implies_action_taken"]
    if not isinstance(implies_action, bool):
        raise ValueError("`implies_action_taken` must be a boolean")

    scope_raw = data.get("scope_category")
    scope_category: ScopeCategory | None
    if scope_raw is None:
        scope_category = None
    elif isinstance(scope_raw, str):
        try:
            scope_category = ScopeCategory(scope_raw)
        except ValueError as e:
            raise ValueError(f"unknown scope_category {scope_raw!r}") from e
    else:
        raise ValueError("`scope_category` must be a string or null")

    return Envelope(
        answer=answer,
        cited_finding_ids=tuple(cited_ids),
        is_in_scope=is_in_scope,
        scope_category=scope_category,
        implies_action_taken=implies_action,
    )


@dataclass(frozen=True)
class ValidationFailure:
    reason: FallbackReason
    detail: str


def _canonical_dollar_universe(
    scan: "ScanResult", plan: "PlanResult | None",
) -> list[float]:
    """Every dollar value the LLM is allowed to cite.

    Scan: per-finding monthly_impact_usd.
    Plan: per-step monthly_impact_usd, total_monthly_impact_usd, and
          each sub-action's est_monthly_savings_usd (which may be 0.0
          for observe-only sub-actions; that's fine — 0 is a valid
          canonical citation).
    """
    out: list[float] = [f.monthly_impact_usd for f in scan.findings]
    if plan is not None:
        out.append(plan.total_monthly_impact_usd)
        for step in plan.steps:
            out.append(step.monthly_impact_usd)
            for sa in (step.recommended_sequence or []):
                out.append(sa.est_monthly_savings_usd)
    return out


def validate_envelope(
    envelope: Envelope,
    *,
    scan: "ScanResult",
    plan: "PlanResult | None",
) -> ValidationFailure | None:
    """Run the validator chain. Return None on success, a typed
    failure on the first rule that fires.

    Order is deliberate: cheapest first, and out-of-scope short-
    circuits cost checks (an out-of-scope envelope has empty citation
    arrays and an empty answer by contract).
    """
    if not envelope.is_in_scope:
        # Out-of-scope envelope rules: arrays empty, scope_category set.
        if envelope.scope_category is None:
            return ValidationFailure(
                FallbackReason.SCHEMA_INVALID,
                "is_in_scope=false requires scope_category",
            )
        if envelope.answer.strip():
            return ValidationFailure(
                FallbackReason.SCHEMA_INVALID,
                "is_in_scope=false requires empty answer (framework owns refusal text)",
            )
        if envelope.cited_finding_ids:
            return ValidationFailure(
                FallbackReason.SCHEMA_INVALID,
                "is_in_scope=false requires empty citation array",
            )
        return None

    # In-scope envelope rules.
    if envelope.implies_action_taken:
        return ValidationFailure(
            FallbackReason.IMPLIED_ACTION,
            "envelope marked implies_action_taken=true",
        )

    known_ids = {f.id for f in scan.findings}
    unknown = [fid for fid in envelope.cited_finding_ids if fid not in known_ids]
    if unknown:
        return ValidationFailure(
            FallbackReason.UNKNOWN_FINDING_ID,
            f"cited_finding_ids not in scan: {unknown}",
        )

    # Past-tense action language regardless of the implies_action_taken
    # flag. The flag is a self-check; the regex catches the cases
    # where the LLM lied to its own envelope.
    if _matches_past_tense_action(envelope.answer):
        return ValidationFailure(
            FallbackReason.IMPLIED_ACTION,
            "answer uses past-tense action language",
        )

    # Dollar rule (regex-strict-rules protocol):
    #
    #   - Arithmetic phrasing + any inline `$` → SYNTHESIZED_COST,
    #     even if the figure coincidentally matches a canonical
    #     value. The planner is the only source of derived totals;
    #     the LLM doing arithmetic in prose is unverifiable.
    #   - Inline `$N` that doesn't match any canonical value
    #     → INVENTED_COST.
    #   - Inline `$N` that matches a canonical value AND no
    #     arithmetic phrasing → pass.
    dollar_matches = list(INLINE_DOLLAR_RE.finditer(envelope.answer))
    if dollar_matches:
        if _looks_synthesized(envelope.answer):
            return ValidationFailure(
                FallbackReason.SYNTHESIZED_COST,
                "answer contains $ figure with arithmetic phrasing "
                "(\"together\", \"total\", \"sum\", percent, etc.)",
            )
        universe = _canonical_dollar_universe(scan, plan)
        for match in dollar_matches:
            # match group 1 is the numeric body without `$`. Strip
            # thousands separators before parsing — the LLM will
            # write "$5,400" and the validator must compare 5400.0.
            try:
                value = float(match.group(1).replace(",", ""))
            except ValueError:
                # Numeric body unparseable — treat as invented.
                return ValidationFailure(
                    FallbackReason.INVENTED_COST,
                    f"answer has unparseable dollar literal {match.group(0)!r}",
                )
            if not any(abs(value - c) <= DOLLAR_TOLERANCE for c in universe):
                return ValidationFailure(
                    FallbackReason.INVENTED_COST,
                    f"answer cites ${value:.2f} which is not in scan/plan universe",
                )

    return None


def _looks_synthesized(answer: str) -> bool:
    """Detect arithmetic phrasing around an inline `$` figure.

    Heuristic — not perfect, but distinguishes the two failure modes
    cleanly enough for the rubric. Tightening lives in its own PR if
    real-LLM output shows false negatives."""
    lowered = answer.lower()
    arithmetic_markers = (
        "together", "combined", "total of", "totals ", "in total",
        "summed", "adds up", "roughly", "approximately", "%",
        "percent", "average", "per year", "/year", "/yr",
    )
    return any(m in lowered for m in arithmetic_markers)


def _matches_past_tense_action(answer: str) -> bool:
    return any(p.search(answer) for p in PAST_TENSE_ACTION_PATTERNS)


# ---------------------------------------------------------------------------
# Freshness resolver
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FreshnessVerdict:
    tier: FreshnessTier
    age_seconds: float
    human_age: str   # for prompt + user-visible text


def freshness_verdict(
    context: ThreadContext,
    *,
    config: "WhisperConfig",
    now: datetime | None = None,
) -> FreshnessVerdict:
    age = context.age_now(now=now)
    aging_after = config.plan_thread_freshness_aging_after_min * 60
    stale_after = config.plan_thread_freshness_stale_after_hours * 3600
    expired_after = config.plan_thread_freshness_expired_after_hours * 3600

    if age >= expired_after:
        tier = FreshnessTier.EXPIRED
    elif age >= stale_after:
        tier = FreshnessTier.STALE
    elif age >= aging_after:
        tier = FreshnessTier.AGING
    else:
        tier = FreshnessTier.FRESH

    return FreshnessVerdict(tier=tier, age_seconds=age, human_age=_format_age(age))


def _format_age(seconds: float) -> str:
    """Round to the largest sensible unit. Match Slack-native phrasing."""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours < 24:
        return f"{hours}h" if minutes == 0 else f"{hours}h {minutes}m"
    days = hours // 24
    rem_hours = hours % 24
    return f"{days}d" if rem_hours == 0 else f"{days}d {rem_hours}h"


# ---------------------------------------------------------------------------
# User-visible deterministic text
# ---------------------------------------------------------------------------

EXPIRED_TEMPLATE = (
    ":hourglass: This plan was generated {age} ago. AWS state and costs "
    "may have changed materially. Run `/whisper scan` then `/whisper plan` "
    "for an up-to-date answer."
)

STALE_PREFIX_TEMPLATE = (
    ":warning: Plan is {age} old; resource state may have changed — run "
    "`/whisper scan` for fresh data.\n\n"
)

AGING_FOOTER_TEMPLATE = "\n\n_(plan is {age} old)_"

DRIFT_TEMPLATE = (
    ":warning: I don't see those findings in the current scan. Run "
    "`/whisper scan` for fresh data and `/whisper plan` to re-plan "
    "against it."
)

INVENTED_COST_TEMPLATE = (
    ":warning: I almost gave you a dollar figure I can't ground in the "
    "scan or plan. Run `/whisper scan` + `/whisper plan` for current "
    "numbers."
)

SYNTHESIZED_COST_TEMPLATE = (
    ":warning: I almost answered with a derived dollar figure (a sum or "
    "percentage). I only quote canonical numbers from the scan and plan. "
    "Run `/whisper plan` to get a fresh plan with the totals you want."
)

IMPLIED_ACTION_TEMPLATE = (
    "I don't take actions from thread messages. To remediate, click the "
    "*Open PR* button on the finding above (where available), or use "
    "`/whisper scan` for per-finding action affordances."
)

PARSE_FAILED_TEMPLATE = (
    ":x: I couldn't produce a clean answer. The model's response didn't "
    "parse. Try rephrasing the question."
)

LLM_UNAVAILABLE_TEMPLATE = (
    ":grey_question: I can't answer follow-up questions without an LLM "
    "configured. Run `whisper-config doctor` to see what's missing."
)


SCOPE_REFUSAL_TEMPLATES: dict[ScopeCategory, str] = {
    ScopeCategory.ACCOUNT_METADATA: (
        ":grey_question: I answer questions about the findings and plan in "
        "this thread. For account-level metadata (account ID, regions, "
        "organisation info), check the AWS console or `aws sts "
        "get-caller-identity`."
    ),
    ScopeCategory.BILLING_PORTAL: (
        ":grey_question: I don't navigate the AWS console for you. Open "
        "the billing or Cost Explorer pages directly in your browser."
    ),
    ScopeCategory.IAM_POLICY: (
        ":grey_question: IAM policy questions are outside the scope of "
        "this plan thread. The `agentic/*.md` docs list the IAM "
        "permissions each pattern requires."
    ),
    ScopeCategory.OTHER: (
        ":grey_question: I can answer questions about the findings and "
        "plan above. That question is outside the scope of this thread."
    ),
}


def render_scope_refusal(category: ScopeCategory) -> str:
    return SCOPE_REFUSAL_TEMPLATES[category]


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

def format_scan_block(scan: "ScanResult") -> str:
    if not scan.findings:
        return "(scan returned zero findings)"
    lines: list[str] = [
        f"Total monthly waste: ${scan.total_monthly_impact_usd:.2f} "
        f"across {scan.finding_count} findings.",
        "",
        "Findings:",
    ]
    for f in scan.sorted_by_impact()[:MAX_FINDINGS_IN_CONTEXT]:
        lines.append(
            f"- id={f.id} pattern={f.pattern_id} resource={f.resource_id} "
            f"region={f.region} monthly_impact_usd={f.monthly_impact_usd:.2f}"
        )
        lines.append(f"    {f.summary}")
    return "\n".join(lines)


def format_plan_block(plan: "PlanResult | None") -> str:
    if plan is None:
        return "(no plan in this thread)"
    if not plan.steps:
        return f"(plan {plan.plan_id[:8]} has zero steps — status={plan.status})"
    lines: list[str] = [
        f"Plan {plan.plan_id[:8]} — status={plan.status}, "
        f"total ${plan.total_monthly_impact_usd:.2f}/mo, "
        f"confidence {plan.confidence:.2f}, goal={plan.goal!r}",
        "",
        "Steps (planner order):",
    ]
    for step in sorted(plan.steps, key=lambda s: s.order_rank)[:MAX_PLAN_STEPS_IN_CONTEXT]:
        lines.append(
            f"- rank={step.order_rank} finding_id={step.finding_id} "
            f"pattern={step.pattern_id} mode={step.suggested_mode} "
            f"monthly_impact_usd={step.monthly_impact_usd:.2f}"
        )
        lines.append(f"    {step.rationale}")
        for sa in (step.recommended_sequence or []):
            lines.append(
                f"      sub: candidate={sa.candidate_id} kind={sa.action_kind} "
                f"savings={sa.est_monthly_savings_usd:.2f} "
                f"tier={sa.evidence_tier}"
            )
    return "\n".join(lines)


def format_turn_history(context: ThreadContext) -> str:
    if not context.turns:
        return "(no prior turns in this thread)"
    out: list[str] = []
    for t in context.turns:
        out.append(f"User: {t.user_question}")
        out.append(f"Assistant: {t.assistant_answer}")
        out.append("")
    return "\n".join(out).rstrip()


def format_plan_age(verdict: FreshnessVerdict) -> str:
    tier = verdict.tier
    if tier == FreshnessTier.FRESH:
        return f"Plan is {verdict.human_age} old — fresh; no stale-tier rules apply."
    if tier == FreshnessTier.AGING:
        return f"Plan is {verdict.human_age} old — aging; standard answer."
    if tier == FreshnessTier.STALE:
        return (
            f"Plan is {verdict.human_age} old — STALE. Use contextualizing "
            "language ('the original plan prioritized...', 'the scan at "
            "the time showed...') rather than confident new recommendations."
        )
    # Expired tier never reaches the prompt — the framework short-circuits.
    return f"Plan is {verdict.human_age} old."


def build_prompt(
    question: str,
    *,
    context: ThreadContext,
    verdict: FreshnessVerdict,
) -> str:
    template = load_template(TEMPLATE_NAME)
    return (
        template.text
        .replace("<<SCAN_BLOCK>>", format_scan_block(context.scan_result))
        .replace("<<PLAN_BLOCK>>", format_plan_block(context.plan_result))
        .replace("<<TURN_HISTORY>>", format_turn_history(context))
        .replace("<<PLAN_AGE>>", format_plan_age(verdict))
        .replace("<<QUESTION>>", question.strip())
    )


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def answer_plan_thread_question(
    question: str,
    *,
    context: ThreadContext,
    client: LLMClient | None = None,
    config: "WhisperConfig | None" = None,
    now: datetime | None = None,
) -> TurnOutcome:
    """Answer a follow-up question against a cached (scan, plan).

    Routes through: freshness gate → pre-router → LLM (one repair
    retry on parse failure) → envelope validators → either
    substituted prose or a typed fallback. The returned TurnOutcome
    carries the surfaced text plus a ConversationTurn the caller is
    expected to record in `context.turns` (the caller controls the
    record-or-not decision so dry-run / preview paths can opt out).
    """
    if context.plan_result is None:
        # This entry point is plan-thread only. The scan-only path
        # uses analyzer.conversation.answer_thread_question.
        raise ValueError(
            "answer_plan_thread_question called with plan_result=None; "
            "use analyzer.conversation.answer_thread_question for scan-only threads"
        )

    # 1. Freshness gate — runs BEFORE any LLM call. Expired plans get
    #    a deterministic refusal; no tokens spent.
    if config is None:
        # Without config we can't even resolve freshness thresholds.
        # Fall back to the no-LLM path so the caller learns the user
        # needs to run `whisper-config doctor`.
        return _finalize_fallback(
            question=question,
            text=LLM_UNAVAILABLE_TEMPLATE,
            reason=FallbackReason.LLM_UNAVAILABLE,
            tier=FreshnessTier.FRESH,
            now=now,
        )

    verdict = freshness_verdict(context, config=config, now=now)
    if verdict.tier == FreshnessTier.EXPIRED:
        text = EXPIRED_TEMPLATE.format(age=verdict.human_age)
        return _finalize_fallback(
            question=question, text=text,
            reason=FallbackReason.EXPIRED, tier=verdict.tier, now=now,
        )

    # 2. Deterministic pre-router — catches obvious out-of-scope and
    #    action-request phrasings without an LLM call.
    pre = pre_route(question)
    if pre is not None:
        return TurnOutcome(
            surfaced_text=pre.message,
            turn=_make_turn(
                question, pre.message, (), "out_of_scope", now=now,
            ),
            fallback=None,  # not a fallback path; deterministic refusal
            freshness_tier=verdict.tier,
            envelope=None,
            parse_retry_count=0,
            pre_routed_category=pre.category,
        )

    # 3. LLM call with one repair retry on parse failure.
    if client is None:
        try:
            client = make_llm_client(config, prompt_template=TEMPLATE_NAME)
        except ValueError as e:
            logger.warning("LLM not configured (%s) — returning fallback", e)
            return _finalize_fallback(
                question=question, text=LLM_UNAVAILABLE_TEMPLATE,
                reason=FallbackReason.LLM_UNAVAILABLE,
                tier=verdict.tier, now=now,
            )

    prompt = build_prompt(question, context=context, verdict=verdict)
    try:
        envelope, retries = _call_with_retry(client, prompt)
    except _LLMCallError as e:
        logger.exception("plan-thread LLM call failed")
        text = f":x: I couldn't answer that right now ({e})."
        return _finalize_fallback(
            question=question, text=text,
            reason=FallbackReason.LLM_UNAVAILABLE,
            tier=verdict.tier, now=now,
        )
    except _ParseFailedError:
        return _finalize_fallback(
            question=question, text=PARSE_FAILED_TEMPLATE,
            reason=FallbackReason.PARSE_FAILED,
            tier=verdict.tier, now=now,
        )

    # 4. Validator chain.
    failure = validate_envelope(
        envelope, scan=context.scan_result, plan=context.plan_result,
    )
    if failure is not None:
        text = _render_fallback(failure.reason)
        return TurnOutcome(
            surfaced_text=text,
            turn=_make_turn(
                question, text, (), _kind_for(failure.reason), now=now,
            ),
            fallback=failure.reason,
            freshness_tier=verdict.tier,
            envelope=envelope,
            parse_retry_count=retries,
            metadata={"validator_detail": failure.detail},
        )

    # 5. Out-of-scope envelope — render the deterministic refusal.
    if not envelope.is_in_scope:
        # validate_envelope already asserted scope_category is set.
        text = render_scope_refusal(envelope.scope_category)  # type: ignore[arg-type]
        return TurnOutcome(
            surfaced_text=text,
            turn=_make_turn(question, text, (), "out_of_scope", now=now),
            fallback=None,
            freshness_tier=verdict.tier,
            envelope=envelope,
            parse_retry_count=retries,
        )

    # 6. Success path: apply freshness wrapper, canonicalise
    #    cited_finding_ids into scan order. The answer text passes
    #    through unchanged — under regex-strict-rules the LLM writes
    #    canonical dollar literals inline; there's nothing to substitute.
    surfaced = _apply_freshness_wrapper(envelope.answer, verdict)
    citations = _canonicalise_citations(
        envelope.cited_finding_ids, scan=context.scan_result,
    )
    kind = "stale_warn" if verdict.tier == FreshnessTier.STALE else "answered"
    return TurnOutcome(
        surfaced_text=surfaced,
        turn=_make_turn(question, surfaced, citations, kind, now=now),
        fallback=None,
        freshness_tier=verdict.tier,
        envelope=envelope,
        parse_retry_count=retries,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

class _LLMCallError(RuntimeError):
    pass


class _ParseFailedError(RuntimeError):
    pass


def _call_with_retry(client: LLMClient, prompt: str) -> tuple[Envelope, int]:
    """Call LLM and parse the envelope. One repair retry on ParseError.

    Returns (envelope, retry_count). Raises `_ParseFailedError` if
    parsing fails on both attempts, or `_LLMCallError` for transport-
    level failures (network, auth, etc.).
    """
    try:
        first = client.complete([Message(role="user", content=prompt)])
    except Exception as e:
        raise _LLMCallError(str(e)) from e

    try:
        return parse_envelope(first.text), 0
    except ValueError as e:
        logger.info("first envelope parse failed (%s); repair retry", e)

    repair_messages = [
        Message(role="user", content=prompt),
        Message(role="assistant", content=first.text),
        Message(role="user", content=REPAIR_INSTRUCTION),
    ]
    try:
        second = client.complete(repair_messages)
    except Exception as e:
        raise _LLMCallError(str(e)) from e

    try:
        return parse_envelope(second.text), 1
    except ValueError as e:
        logger.warning("repair retry also failed to parse envelope: %s", e)
        raise _ParseFailedError(str(e)) from e


def _render_fallback(reason: FallbackReason) -> str:
    return {
        FallbackReason.UNKNOWN_FINDING_ID: DRIFT_TEMPLATE,
        FallbackReason.INVENTED_COST: INVENTED_COST_TEMPLATE,
        FallbackReason.SYNTHESIZED_COST: SYNTHESIZED_COST_TEMPLATE,
        FallbackReason.IMPLIED_ACTION: IMPLIED_ACTION_TEMPLATE,
        FallbackReason.PARSE_FAILED: PARSE_FAILED_TEMPLATE,
        FallbackReason.SCHEMA_INVALID: PARSE_FAILED_TEMPLATE,
        FallbackReason.EXPIRED: EXPIRED_TEMPLATE,
        FallbackReason.LLM_UNAVAILABLE: LLM_UNAVAILABLE_TEMPLATE,
    }[reason]


def _apply_freshness_wrapper(answer: str, verdict: FreshnessVerdict) -> str:
    if verdict.tier == FreshnessTier.STALE:
        return STALE_PREFIX_TEMPLATE.format(age=verdict.human_age) + answer
    if verdict.tier == FreshnessTier.AGING:
        return answer + AGING_FOOTER_TEMPLATE.format(age=verdict.human_age)
    return answer


def _canonicalise_citations(
    cited: tuple[str, ...], *, scan: "ScanResult",
) -> tuple[str, ...]:
    """Re-order cited IDs into scan-impact order (sorted_by_impact),
    dedup, drop any unknown IDs defensively.

    Same principle as RenderablePlan.steps' planner-order sort: the
    LLM emission order is not authoritative. The validator has already
    confirmed no unknown IDs at this point; the defensive drop catches
    a future regression where the validator changes shape."""
    canonical_order = [f.id for f in scan.sorted_by_impact()]
    cited_set = set(cited)
    return tuple(fid for fid in canonical_order if fid in cited_set)


def _make_turn(
    question: str,
    surfaced_text: str,
    citations: tuple[str, ...],
    kind: str,
    *,
    now: datetime | None,
) -> ConversationTurn:
    return ConversationTurn(
        user_question=question.strip(),
        assistant_answer=surfaced_text,
        cited_finding_ids=citations,
        turn_kind=kind,
        created_at=now if now is not None else datetime.now(timezone.utc),
    )


# Map fallback reasons to the `turn_kind` value recorded on the turn
# ring. UNKNOWN_FINDING_ID uses the user-facing label "drift" because
# that's how the rubric and the user-visible template refer to the
# failure mode; the planner's DropReason → message conventions don't
# apply at the conversation surface.
_TURN_KIND_FOR_REASON: dict[FallbackReason, str] = {
    FallbackReason.EXPIRED: "expired",
    FallbackReason.LLM_UNAVAILABLE: "llm_unavailable",
    FallbackReason.PARSE_FAILED: "parse_failed",
    FallbackReason.SCHEMA_INVALID: "parse_failed",
    FallbackReason.UNKNOWN_FINDING_ID: "drift",
    FallbackReason.INVENTED_COST: "invented_cost",
    FallbackReason.SYNTHESIZED_COST: "synthesized_cost",
    FallbackReason.IMPLIED_ACTION: "implied_action",
}


def _kind_for(reason: FallbackReason) -> str:
    return _TURN_KIND_FOR_REASON[reason]


def _finalize_fallback(
    *,
    question: str,
    text: str,
    reason: FallbackReason,
    tier: FreshnessTier,
    now: datetime | None,
) -> TurnOutcome:
    return TurnOutcome(
        surfaced_text=text,
        turn=_make_turn(question, text, (), _kind_for(reason), now=now),
        fallback=reason,
        freshness_tier=tier,
        envelope=None,
        parse_retry_count=0,
    )
