"""
Plan presenters — render a planner.PlanResult for human surfaces.

Three presenters share a single intermediate: `RenderablePlan`. The CLI
and the Slack handler both call `to_renderable(plan, findings)` exactly
once, then hand the result to whichever surface presenter they need.
The intermediate is the contract: any field a surface wants to render
must live here, derived once by the same code path.

Drift between Slack-fixes-rendering-bug-X and CLI-still-has-rendering-
bug-X is the failure mode this module guards against.

CRITICAL invariants the rendering layer preserves (see CLAUDE.md
"LLM proposes; framework disposes"):

  - Steps are surfaced in the planner's `order_rank` order. Presenters
    MUST NOT re-sort. `to_renderable` sorts once and freezes the
    sequence; the surfaces iterate it as-is.
  - The mode badge for each step is the literal `suggested_mode` from
    the planner, formatted as `[mode]`. dry_run / command / pr /
    api_call are distinct, never flattened to a generic "fix this".
  - `is_safe_executable` is the single source of truth for whether a
    surface may attach an executable affordance (button, etc.). It is
    derived from the modes contract:
        step.suggested_mode in finding.available_modes
        AND step.suggested_mode != "dry_run"
    NOT from `safe_to_fix` alone — `safe_to_fix` is a pattern-level
    concept that future patterns may decouple from UI executability.
  - Dropped steps are NEVER rendered in human surfaces. The count may
    appear; the contents (finding ids, rationales, raw emissions) must
    stay in the JSON trace for debugging only.
  - `total_monthly_impact_usd` rendered to humans is the canonical
    planner value, not re-summed by the renderer. Re-summing in the
    renderer would silently diverge if the canonical computation
    changed.

PR #8 scope decision: only `pr`-mode steps get an executable button in
Slack. `command` and `api_call` modes render as text-only mode badges.
See `agentic/plan_surface_agentic.md` for the rationale.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

_SRC = Path(__file__).parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent.modes import AvailableModesResolver  # noqa: E402

from ._slack_text import (  # noqa: E402
    SLACK_MAX_MRKDWN_CHARS,
    escape_mrkdwn,
    safe_mrkdwn,
    safe_mrkdwn_code,
)

if TYPE_CHECKING:
    from agent.schemas import PlanResult
    from patterns.base import Finding


# Versioned contract surface for JSON output. Bump when the on-the-wire
# shape of RenderablePlan changes in a way external consumers can see.
RENDERABLE_SCHEMA_VERSION = "1"

# Default goal echo when the planner used DEFAULT_GOAL.
DEFAULT_GOAL_ECHO = "(default: rank by impact and risk)"

# Slack hard limit per chat.postMessage call. Exceeding this returns
# `invalid_blocks` and the message is rejected. The renderer enforces
# the limit by truncating tail steps and emitting a "more not shown"
# footer pointing at the CLI — degraded but correct (see
# agentic/plan_surface_agentic.md).
SLACK_MAX_BLOCKS = 50

# Per-field length budgets for Slack mrkdwn elements. Each block we
# render must keep its composed text under SLACK_MAX_MRKDWN_CHARS
# (3000). The budgets below sum to under that limit per block once
# decorators (titles, prefixes, separators) are included — see the
# composition arithmetic in TestSlackTextLengthBudget. LLM responses
# routinely run long; clipping makes truncation visible to the user
# rather than silently dropping the message.
MAX_GOAL_LEN = 500
MAX_SUMMARY_LEN = 2400
MAX_RATIONALE_LEN = 2500
MAX_SUB_RATIONALE_LEN = 1500
MAX_RESOURCE_ID_LEN = 200
MAX_CANDIDATE_ID_LEN = 200


def mode_badge(mode: str) -> str:
    """Bracket-form mode label. Used in CLI and Slack text identically."""
    return f"[{mode}]"


# ---------------------------------------------------------------------------
# Shared intermediate
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RenderableSubAction:
    """A single sub-action recommendation under a step (p006-shaped).

    Carries the canonical (validator-promoted) values, not what the LLM
    typed. The renderer reads these verbatim.
    """
    candidate_id: str
    action_kind: str               # "add_vpc_endpoint" | "observe_and_reassess"
    est_monthly_savings_usd: float  # canonical, $0 for observe_and_reassess
    evidence_tier: str              # "observed" | "inferred"
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "action_kind": self.action_kind,
            "est_monthly_savings_usd": round(self.est_monthly_savings_usd, 2),
            "evidence_tier": self.evidence_tier,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class RenderablePlanStep:
    """One row of a plan, ready for any surface to display.

    Built once by `to_renderable`. Surfaces consume it; surfaces must
    not derive new safety- or executability-related fields.
    """
    finding_id: str
    pattern_id: str
    resource_id: str               # joined from Finding for display
    order_rank: int
    monthly_impact_usd: float      # canonical from PlanStep
    rationale: str
    mode: str                      # literal suggested_mode
    mode_label: str                # mode_badge(mode), pre-formatted
    is_safe_executable: bool       # see module docstring
    sub_actions: tuple[RenderableSubAction, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "pattern_id": self.pattern_id,
            "resource_id": self.resource_id,
            "order_rank": self.order_rank,
            "monthly_impact_usd": round(self.monthly_impact_usd, 2),
            "rationale": self.rationale,
            "mode": self.mode,
            "mode_label": self.mode_label,
            "is_safe_executable": self.is_safe_executable,
            "sub_actions": [s.to_dict() for s in self.sub_actions],
        }


@dataclass(frozen=True)
class RenderablePlan:
    """The shared intermediate. The contract.

    Fields are deliberately the minimum set every surface needs. If a
    surface decides it needs more data, STOP and surface — add to this
    dataclass deliberately, not opportunistically inside the surface.
    """
    plan_id: str
    goal: str | None
    status: str                          # "ok" | "validation_failed"
    summary: str
    total_monthly_impact_usd: float      # canonical from PlanResult
    confidence: float
    steps: tuple[RenderablePlanStep, ...]
    dropped_step_count: int              # count only, never contents
    trace: dict[str, Any] = field(default_factory=dict)
    # Trace carries:
    #   parse_retry_count: int
    #   model:             str
    #   provider:          str
    # CLI may surface these in the failure path. Slack does not.

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RENDERABLE_SCHEMA_VERSION,
            "plan_id": self.plan_id,
            "goal": self.goal,
            "status": self.status,
            "summary": self.summary,
            "total_monthly_impact_usd": round(self.total_monthly_impact_usd, 2),
            "confidence": round(self.confidence, 3),
            "steps": [s.to_dict() for s in self.steps],
            "dropped_step_count": self.dropped_step_count,
            "trace": dict(self.trace),
        }


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def to_renderable(
    plan: "PlanResult",
    findings: list["Finding"],
    *,
    resolver: AvailableModesResolver | None = None,
) -> RenderablePlan:
    """Build the shared intermediate. Call once per render.

    `findings` must be the same list the planner ran against (we look
    up each step's source finding to compute `is_safe_executable` and
    pull the display `resource_id`). A step whose finding is absent
    from `findings` still renders, but with `is_safe_executable=False`
    and a placeholder resource id — defensive fallback, not an
    expected path.
    """
    resolver = resolver or AvailableModesResolver()
    findings_by_id = {f.id: f for f in findings}

    # Preserve canonical planner ordering. Surfaces may not re-sort.
    ordered = sorted(plan.steps, key=lambda s: s.order_rank)

    renderable_steps: list[RenderablePlanStep] = []
    for step in ordered:
        finding = findings_by_id.get(step.finding_id)
        if finding is None:
            available_modes: set[str] = set()
            resource_id = "(unknown)"
        else:
            available_modes = resolver.resolve_values(finding)
            resource_id = finding.resource_id

        # is_safe_executable derives from the modes contract, NOT from
        # safe_to_fix alone. The validator has already enforced that
        # step.suggested_mode is in available_modes for any kept step;
        # we re-check defensively so future changes to the contract
        # surface through this single boolean instead of every renderer.
        is_safe_executable = (
            step.suggested_mode in available_modes
            and step.suggested_mode != "dry_run"
        )

        subs = tuple(
            RenderableSubAction(
                candidate_id=sa.candidate_id,
                action_kind=sa.action_kind,
                est_monthly_savings_usd=sa.est_monthly_savings_usd,
                evidence_tier=sa.evidence_tier,
                rationale=sa.rationale,
            )
            for sa in (step.recommended_sequence or [])
        )

        renderable_steps.append(RenderablePlanStep(
            finding_id=step.finding_id,
            pattern_id=step.pattern_id,
            resource_id=resource_id,
            order_rank=step.order_rank,
            monthly_impact_usd=step.monthly_impact_usd,
            rationale=step.rationale,
            mode=step.suggested_mode,
            mode_label=mode_badge(step.suggested_mode),
            is_safe_executable=is_safe_executable,
            sub_actions=subs,
        ))

    return RenderablePlan(
        plan_id=plan.plan_id,
        goal=plan.goal,
        status=plan.status,
        summary=plan.summary,
        total_monthly_impact_usd=plan.total_monthly_impact_usd,
        confidence=plan.confidence,
        steps=tuple(renderable_steps),
        dropped_step_count=len(plan.dropped_steps),
        trace={
            "parse_retry_count": plan.parse_retry_count,
            "model": plan.model,
            "provider": plan.provider,
        },
    )


# ---------------------------------------------------------------------------
# Surface presenters
# ---------------------------------------------------------------------------

def _short_id(plan_id: str) -> str:
    return plan_id.split("-")[0] if plan_id else "?"


def _goal_echo(goal: str | None) -> str:
    return goal if (goal and goal.strip()) else DEFAULT_GOAL_ECHO


def _sub_action_hedge(sa: RenderableSubAction) -> str:
    """Tier-aware hedge label. Inferred candidates render as
    investigate-first; observed candidates render with the canonical
    savings figure."""
    if sa.evidence_tier == "inferred":
        return "(inferred · investigate first)"
    return f"(observed · ${sa.est_monthly_savings_usd:.2f}/mo)"


class TextPlanPresenter:
    """Plain text plan renderer. CLI default.

    No colors, no rich formatting — greppable, CI-friendly, matches the
    style of `cli/doctor.py`.

    The CLI failure path is verbose: it surfaces parse_retry_count and
    model so an operator can debug. Slack is intentionally different
    (see BlockKitPlanPresenter._render_failure).
    """

    def render(self, plan: RenderablePlan) -> str:
        if plan.status == "validation_failed" and not plan.steps:
            return self._render_failure(plan)
        return self._render_ok(plan)

    # ---- internal ----

    def _render_failure(self, plan: RenderablePlan) -> str:
        retry = plan.trace.get("parse_retry_count", 0)
        model = plan.trace.get("model", "unknown")
        provider = plan.trace.get("provider", "unknown")
        lines = [
            f"AWS Bill Whisperer — Plan {_short_id(plan.plan_id)}",
            f"Status: {plan.status}  ·  0 step(s)",
            "The planner did not produce a usable plan.",
            f"  (parse_retry_count={retry}, model={model}, provider={provider})",
            "Run with --format json for the raw trace.",
        ]
        return "\n".join(lines) + "\n"

    def _render_ok(self, plan: RenderablePlan) -> str:
        lines = [
            f"AWS Bill Whisperer — Plan {_short_id(plan.plan_id)}",
            f"Goal: {_goal_echo(plan.goal)}",
            (
                f"Status: {plan.status}  ·  {len(plan.steps)} step(s)  ·  "
                f"${plan.total_monthly_impact_usd:.2f}/mo  ·  "
                f"confidence {plan.confidence:.2f}"
            ),
            "",
            plan.summary,
            "",
        ]
        for step in plan.steps:
            lines.extend(self._render_step(step))
            lines.append("")

        if plan.dropped_step_count > 0:
            lines.append(
                f"({plan.dropped_step_count} emission(s) failed validation "
                "and were excluded.)"
            )
            lines.append("")

        return "\n".join(lines)

    def _render_step(self, step: RenderablePlanStep) -> list[str]:
        observe_hint = "   (observe-only)" if step.mode == "dry_run" else ""
        # Pad mode label to keep columns aligned; widest is "[api_call]" (10 chars).
        mode_col = f"{step.mode_label:<11s}"
        out = [
            (
                f"  {step.order_rank}. {mode_col} p{step.pattern_id}  "
                f"{step.resource_id:<22s}  ${step.monthly_impact_usd:.2f}/mo"
                f"{observe_hint}"
            ),
            f"     {step.rationale}",
        ]
        for sa in step.sub_actions:
            hedge = _sub_action_hedge(sa)
            out.append(
                f"     · {sa.action_kind}  {sa.candidate_id}  {hedge}"
            )
            out.append(f"       {sa.rationale}")
        return out


class JSONPlanPresenter:
    """JSON serialization of RenderablePlan.

    Versioned contract surface. No renderer-derived fields, no cosmetic
    drift. Stable across UI changes — downstream tooling can depend on
    this shape. Bump RENDERABLE_SCHEMA_VERSION on incompatible changes.
    """

    def render(self, plan: RenderablePlan) -> str:
        return json.dumps(plan.to_dict(), indent=2, default=str)


class BlockKitPlanPresenter:
    """Slack Block Kit renderer.

    Returns a list[dict] ready for `chat.postMessage(blocks=...)`. The
    Slack failure path is intentionally less verbose than the CLI's:
    shared-channel messages don't need to surface implementation details
    (model name, provider, retry count) that only matter to whoever runs
    the system.

    PR #8 scope: only PR-mode steps get an executable button. See
    agentic/plan_surface_agentic.md for the rationale.
    """

    def render(self, plan: RenderablePlan) -> list[dict[str, Any]]:
        if plan.status == "validation_failed" and not plan.steps:
            return self._render_failure(plan)
        return self._render_ok(plan)

    # ---- internal ----

    def _render_failure(self, plan: RenderablePlan) -> list[dict[str, Any]]:
        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "AWS Bill Whisperer — Plan"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "The planner did not produce a usable plan. "
                        "_(validation failed after retries)_"
                    ),
                },
            },
        ]

    def _render_ok(self, plan: RenderablePlan) -> list[dict[str, Any]]:
        head = self._head_blocks(plan)
        dropped_footer = self._dropped_footer_blocks(plan)
        step_sections = [self._step_section_blocks(s) for s in plan.steps]
        full_step_blocks = [b for sec in step_sections for b in sec]

        naive_total = len(head) + len(full_step_blocks) + len(dropped_footer)
        if naive_total <= SLACK_MAX_BLOCKS:
            return head + full_step_blocks + dropped_footer

        # Truncate. Reserve space for the truncation footer (2 blocks:
        # divider + context) so the user knows the rest exists.
        truncation_footer_size = 2
        budget = (
            SLACK_MAX_BLOCKS
            - len(head)
            - len(dropped_footer)
            - truncation_footer_size
        )
        shown_blocks: list[dict[str, Any]] = []
        shown_count = 0
        for sec in step_sections:
            if len(shown_blocks) + len(sec) > budget:
                break
            shown_blocks.extend(sec)
            shown_count += 1

        tail = plan.steps[shown_count:]
        tail_impact = sum(s.monthly_impact_usd for s in tail)
        truncation_footer = [
            {"type": "divider"},
            {
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": (
                        f"_{len(tail)} more step(s) totaling "
                        f"${tail_impact:.2f}/mo not shown — run "
                        "`whisper-plan` for the full plan._"
                    ),
                }],
            },
        ]
        return head + shown_blocks + truncation_footer + dropped_footer

    def _head_blocks(self, plan: RenderablePlan) -> list[dict[str, Any]]:
        # Goal and summary are user / LLM input — escape angle brackets
        # AND clip to per-field budgets so the composed section text
        # stays under SLACK_MAX_MRKDWN_CHARS even with verbose LLM output.
        safe_goal = safe_mrkdwn(_goal_echo(plan.goal), MAX_GOAL_LEN)
        safe_summary = safe_mrkdwn(plan.summary, MAX_SUMMARY_LEN)
        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "AWS Bill Whisperer — Plan"},
            },
            {
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": (
                        f":compass: *{len(plan.steps)}* steps  ·  "
                        f":moneybag: *${plan.total_monthly_impact_usd:.2f}/mo*  ·  "
                        f"confidence {plan.confidence:.0%}"
                    ),
                }],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"_Goal:_ {safe_goal}\n{safe_summary}",
                },
            },
        ]

    def _dropped_footer_blocks(self, plan: RenderablePlan) -> list[dict[str, Any]]:
        if plan.dropped_step_count <= 0:
            return []
        return [
            {"type": "divider"},
            {
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": (
                        f"_{plan.dropped_step_count} emission(s) failed validation "
                        "and were excluded._"
                    ),
                }],
            },
        ]

    def _step_section_blocks(self, step: RenderablePlanStep) -> list[dict[str, Any]]:
        """All blocks for one step including the leading divider.

        Length must match `step_block_cost(step)` so the truncation
        budget math is exact.
        """
        return [{"type": "divider"}, *self._render_step_blocks(step)]

    def _render_step_blocks(self, step: RenderablePlanStep) -> list[dict[str, Any]]:
        observe_hint = "  _(observe-only)_" if step.mode == "dry_run" else ""
        # resource_id can carry user-controlled content (AWS tag-driven
        # naming) — escape AND strip backticks since it lives in a code span.
        safe_resource = safe_mrkdwn_code(step.resource_id, MAX_RESOURCE_ID_LEN)
        title = (
            f"*{step.order_rank}. {step.mode_label} p{step.pattern_id} — "
            f"`{safe_resource}` — ${step.monthly_impact_usd:.2f}/mo*"
            f"{observe_hint}"
        )
        out: list[dict[str, Any]] = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    # Rationale is LLM output → escape + clip to per-field budget.
                    "text": f"{title}\n{safe_mrkdwn(step.rationale, MAX_RATIONALE_LEN)}",
                },
            }
        ]
        for sa in step.sub_actions:
            hedge = _sub_action_hedge(sa)
            # action_kind is a closed enum (safe); candidate_id is in a
            # code span (escape + strip backticks); rationale is mrkdwn.
            out.append({
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": (
                        f"_{sa.action_kind}_  "
                        f"`{safe_mrkdwn_code(sa.candidate_id, MAX_CANDIDATE_ID_LEN)}`  "
                        f"{hedge}\n{safe_mrkdwn(sa.rationale, MAX_SUB_RATIONALE_LEN)}"
                    ),
                }],
            })
        # PR-only button. Command and api_call modes are text-only in PR #8.
        # The button reuses the existing open_pr action_id so the existing
        # actions.py handler picks it up unchanged. The button's `value`
        # field is finding_id (deterministic UUID), no escaping needed.
        if step.is_safe_executable and step.mode == "pr":
            out.append({
                "type": "actions",
                "elements": [{
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Open PR"},
                    "style": "primary",
                    "value": step.finding_id,
                    "action_id": "open_pr",
                }],
            })
        return out


def step_block_cost(step: RenderablePlanStep) -> int:
    """Block count contributed by one step including its leading divider.

    Public so tests can pin the budget math. Mirrors the structure of
    `BlockKitPlanPresenter._step_section_blocks`:
      - 1 divider
      - 1 section (title + rationale)
      - N sub-action context blocks
      - +1 actions block iff is_safe_executable AND mode == "pr"
    """
    cost = 2 + len(step.sub_actions)
    if step.is_safe_executable and step.mode == "pr":
        cost += 1
    return cost
