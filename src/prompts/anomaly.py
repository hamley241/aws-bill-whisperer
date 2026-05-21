"""Anomaly-detection prompt — sudden spikes, unusual services, etc."""

from . import PromptTemplate


TEMPLATE = PromptTemplate(
    name="anomaly",
    description="Identify and explain anomalies in a cost dataset.",
    text="""Analyze this AWS cost data for anomalies.

An anomaly is:
- A sudden spike (>50% day-over-day increase)
- An unusual service appearing in top costs
- Costs in unexpected regions
- Significant deviation from the trend

For each anomaly found, explain:
1. What the anomaly is
2. Likely cause (if determinable)
3. Recommended action

Cost data:
""",
)
