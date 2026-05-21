"""Recommendations prompt — focused cost-optimization opportunities."""

from . import PromptTemplate


TEMPLATE = PromptTemplate(
    name="recommendations",
    description="Specific, prioritized cost-optimization recommendations.",
    text="""Based on this AWS cost data, identify specific cost optimization opportunities.

Focus on:
1. Unused or idle resources (stopped instances still incurring costs, unattached EBS volumes)
2. Right-sizing opportunities (overprovisioned instances)
3. Reserved Instance / Savings Plan opportunities
4. Storage optimization (S3 lifecycle policies, EBS snapshot cleanup)
5. Data transfer costs (consider CloudFront, VPC endpoints)

For each recommendation:
- Be specific about what to do
- Estimate the savings (monthly)
- Rate the effort (easy/medium/hard)

Cost data:
""",
)
