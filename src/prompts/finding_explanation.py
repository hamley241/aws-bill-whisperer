"""Per-finding plain-English explanation template.

Used by analyzer.explainer to expand a finding's `summary` (machine-
generated, one line) into a short, human-friendly `explanation`. The
template assumes the model sees enough evidence to make a confident
claim and otherwise prefers honesty over speculation.
"""

from . import PromptTemplate


TEMPLATE = PromptTemplate(
    name="finding_explanation",
    description="Concise plain-English explanation of a single waste finding.",
    text="""You are explaining a single AWS cost-waste finding to a platform engineer
who knows AWS but may not know this specific service's pricing details.

Write **2-3 sentences max**. Be specific. Be honest about uncertainty.
Do NOT repeat the resource ID or the dollar amount — those are already
displayed alongside your explanation. Focus on *why* this is waste and
*what the fix does* (and any non-obvious risk).

If the evidence is thin or the cost is small, say so plainly rather
than invent a story.

## Finding

- Resource: {resource_type}
- Region: {region}
- Risk tier: {risk_tier}
- Summary (machine-generated): {summary}
- Suggested fix command: {fix_command}
- Evidence: {evidence_json}

## Output

Your explanation (2-3 sentences, no markdown headings, no bullet lists):
""",
)
