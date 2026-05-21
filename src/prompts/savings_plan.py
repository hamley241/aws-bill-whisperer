"""Savings-plan template — input for the agent.SavingsPlanner.

Convention note: every other prompt in this repo (cost_analysis,
anomaly, recommendations, finding_explanation, thread_reply) lives at
`src/prompts/<name>.py` and exposes a `TEMPLATE: PromptTemplate`. The
PR 2 brief proposed `src/agent/prompts/savings_plan.md`, but that
would split the prompt registry across two locations and stop
`load_template()` from finding it. Keeping the convention here so
`load_template("savings_plan")` Just Works.

Placeholder convention: this template embeds JSON schema examples that
contain literal `{` and `}`, so we can't use `str.format`. Placeholders
are angle-bracketed sentinels (`<<GOAL>>`, `<<FINDINGS_BLOCK>>`) the
planner substitutes via `str.replace`.
"""

from . import PromptTemplate


_TEXT = """You are a FinOps planner. You are given a list of cost-waste findings from one AWS account. Your job: produce a single ordered remediation plan toward the stated goal.

## Hard rules — you will be evaluated against these

1. Every `finding_id` in your output MUST appear in the input findings list below. NEVER invent or modify finding IDs.
2. Every `suggested_mode` MUST come from the `available_modes` list shown next to each finding. NEVER recommend a mode that isn't listed there.
3. Every `monthly_impact_usd` MUST equal the corresponding finding's `monthly_impact_usd` exactly (within $0.01). Copy the number; do not adjust.
4. At most one step per `finding_id`. If two findings are related, prefer the one with higher impact.
5. Output is JSON only — no markdown commentary, no code fences in your final answer.

Steps you cite must come from this input. Findings not worth touching can simply be omitted from your plan.

## Output schema

{
  "summary": "1-2 sentences explaining the plan and any tradeoffs you considered",
  "steps": [
    {
      "finding_id": "<one of the input finding IDs>",
      "suggested_mode": "<one of the finding's available_modes>",
      "monthly_impact_usd": <number, must equal the finding's value>,
      "rationale": "1-2 sentences specific to THIS finding",
      "order_rank": <integer; lower numbers run first>
    }
  ]
}

## Goal

<<GOAL>>

## Findings

<<FINDINGS_BLOCK>>

## Your plan (JSON only)
"""


TEMPLATE = PromptTemplate(
    name="savings_plan",
    description="One-shot ordered savings plan over a fixed set of findings.",
    text=_TEXT,
    version="v1",
)


REPAIR_INSTRUCTION = (
    "Your previous response did not parse as a single JSON object. "
    "Re-emit ONLY a JSON object matching the schema you were given. "
    "No prose before or after. No markdown fences."
)
