"""LLM abstraction for cost analysis using Bedrock or OpenAI."""

import json
import logging
import os

import boto3

from .prompts import ANALYSIS_PROMPT

logger = logging.getLogger(__name__)


def analyze_costs(cost_data: dict, provider: str = "bedrock", model: str | None = None) -> str:
    """
    Send cost data to LLM and get analysis back.

    Args:
        cost_data: Dictionary containing AWS cost data from cost_explorer
        provider: "bedrock" or "openai"
        model: Optional model override

    Returns:
        Analysis text in markdown format
    """
    # Format cost data as readable text for the LLM
    cost_text = _format_cost_data_for_llm(cost_data)
    full_prompt = ANALYSIS_PROMPT + "\n\n" + cost_text

    if provider == "bedrock":
        return _analyze_bedrock(full_prompt, model)
    elif provider == "openai":
        return _analyze_openai(full_prompt, model)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


def _format_cost_data_for_llm(cost_data: dict) -> str:
    """Convert structured cost data to readable text for LLM."""
    lines = []

    # Current period summary
    if "usage" in cost_data:
        usage = cost_data["usage"]
        lines.append(f"## Current Period: {usage['period']['start']} to {usage['period']['end']}")
        lines.append(f"**Total Cost: ${usage['total']:,.2f}**\n")

        lines.append("### Costs by Service:")
        for svc in usage.get("services", [])[:15]:  # Top 15
            lines.append(f"- {svc['name']}: ${svc['cost']:,.2f} ({svc['percent']}%)")
        lines.append("")

    # Comparison to previous period
    if "comparison" in cost_data:
        comp = cost_data["comparison"]
        direction = "increased" if comp["change"] > 0 else "decreased"
        lines.append("### Comparison to Previous Period:")
        lines.append(f"- Previous period: ${comp['previous']['total']:,.2f}")
        lines.append(f"- Current period: ${comp['current']['total']:,.2f}")
        change_str = f"${comp['change']:+,.2f} ({comp['change_percent']:+.1f}%)"
        lines.append(f"- Change: {change_str} - {direction}")
        lines.append("")

        if comp.get("service_changes"):
            lines.append("### Biggest Service Changes:")
            for svc in comp["service_changes"][:5]:
                prev = f"${svc['previous']:,.2f}"
                curr = f"${svc['current']:,.2f}"
                pct = f"{svc['change_percent']:+.1f}%"
                lines.append(f"- {svc['name']}: {prev} → {curr} ({pct})")
            lines.append("")

    # Regional breakdown
    if "regions" in cost_data:
        regions = cost_data["regions"]
        lines.append("### Costs by Region:")
        for region in regions.get("regions", [])[:10]:
            lines.append(f"- {region['name']}: ${region['cost']:,.2f} ({region['percent']}%)")
        lines.append("")

    # Daily trend (summarized)
    if "daily" in cost_data:
        daily = cost_data["daily"]
        if daily:
            avg_daily = sum(d["cost"] for d in daily) / len(daily)
            max_day = max(daily, key=lambda d: d["cost"])
            min_day = min(daily, key=lambda d: d["cost"])
            lines.append("### Daily Cost Summary:")
            lines.append(f"- Average daily cost: ${avg_daily:,.2f}")
            lines.append(f"- Highest day: {max_day['date']} (${max_day['cost']:,.2f})")
            lines.append(f"- Lowest day: {min_day['date']} (${min_day['cost']:,.2f})")

    return "\n".join(lines)


def _analyze_bedrock(prompt: str, model: str | None = None) -> str:
    """Call AWS Bedrock with Claude."""
    model_id = model or "anthropic.claude-3-sonnet-20240229-v1:0"

    client = boto3.client('bedrock-runtime')

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    })

    try:
        response = client.invoke_model(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=body
        )

        response_body = json.loads(response['body'].read())
        return response_body['content'][0]['text']

    except client.exceptions.AccessDeniedException:
        logger.error("Access denied to Bedrock. Check bedrock:InvokeModel permission.")
        raise
    except Exception as e:
        logger.error(f"Bedrock invocation failed: {e}")
        raise


def _analyze_openai(prompt: str, model: str | None = None) -> str:
    """Call OpenAI API."""
    try:
        import openai
    except ImportError as e:
        raise ImportError(
            "openai package required for OpenAI provider. Run: pip install openai"
        ) from e

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable required for OpenAI provider")

    model_id = model or "gpt-4o"
    client = openai.OpenAI(api_key=api_key)

    try:
        response = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": "You are an expert AWS cost analyst."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=4096
        )
        return response.choices[0].message.content

    except Exception as e:
        logger.error(f"OpenAI API call failed: {e}")
        raise
