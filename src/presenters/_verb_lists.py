"""
Verb lists for plan-surface safety-wording invariants.

The plan renderer (`presenters/plan.py`) and the rendering-invariant test
(`tests/test_plan_rendering.py::TestRenderingPreservesGuarantees`) both
consult these lists. Keeping the lists here (not inline in the test)
means future additions are one edit — drift between "renderer wording"
and "what the test enforces" is the failure mode this guards against.

INVARIANT (enforced by the test):

    For every PlanStep whose source Finding has safe_to_fix=False:
      - the rendered rationale (text + Slack Block Kit) MUST contain
        at least one HEDGED_VERBS entry (case-insensitive substring),
        AND
      - the rendered rationale MUST contain zero UNHEDGED_VERBS entries.

The principle being protected: a step the planner correctly marked as
observe-only must read to the human as observe-only. The framework
disposed; the rendering layer must not re-promise execution.

Matching rules:
  - Case-insensitive substring match. Pick forms generously — substrings
    catch conjugations ("observed", "investigates", "reassessment" all
    match "observe", "investigate", "reassess").
  - Both lists are intentionally narrow at the unhedged end and broader
    at the hedged end. The cost of a false-negative on the hedged check
    is a fixture re-roll; the cost of a false-positive on the unhedged
    check is shipping unsafe wording. Bias toward strict rejection of
    action language; bias toward generous acceptance of observe language.
"""

from __future__ import annotations


# Tokens that signal observation / deferral / awareness-of-safety.
# Includes both verbs (observe, investigate, reassess, consider) and
# adjacent hedging phrases / mode references that mean the same thing
# in context.
HEDGED_VERBS: tuple[str, ...] = (
    # The four canonical examples from the PR contract.
    "observe",
    "investigate",
    "reassess",
    "consider",
    # Adjacent hedge verbs in the same semantic family.
    "evaluate",
    "review",
    "monitor",
    "collect",
    "surface",
    # Observation-prep / data-gathering verbs.
    "enable",      # e.g. "enable Flow Logs and re-scan"
    "rescan",
    "re-scan",
    # Explicit counterfactual / hedging phrases.
    "would be undone",
    "guess",       # e.g. "rather than guessing"
    # Strongest possible hedge — explicit dry_run reference. If the
    # rationale literally names the dry_run mode it can't be misread
    # as recommending execution.
    "dry_run",
    "dry run",
)


# Tokens that signal concrete, executable action language. A rationale
# for an observe-only step must contain ZERO of these.
#
# Kept narrow on purpose. Single bare verbs like "stop" or "delete" are
# excluded because they appear legitimately in observe-only rationales
# ("a stop would be undone by the ASG", "the deletion candidate is...").
# Only forms that promise action go here.
UNHEDGED_VERBS: tuple[str, ...] = (
    "will delete",
    "will stop",
    "will remove",
    "will terminate",
    "will apply",
    "will execute",
    "executes",
    "auto-execute",
    "auto-apply",
    "auto-applies",
    "runs the command",
    "applies the change",
)
