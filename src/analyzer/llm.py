"""
Cost-analysis LLM entry point.

This module is a thin wrapper now: it loads the canonical cost_analysis
prompt template, builds the message list, and delegates to an LLMClient.
All provider-specific code lives in src/llm/. All prompt text lives in
src/prompts/.

Callers can either pass a pre-built LLMClient or rely on the convenience
path that constructs one from WhisperConfig.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Allow `analyzer/` to import sibling `llm/`, `config`, `prompts/` when the
# analyzer is run as part of the Lambda handler (which adds src/ to path).
_SRC = Path(__file__).parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from llm import LLMClient, Message, make_llm_client  # noqa: E402
from prompts import load_template  # noqa: E402

logger = logging.getLogger(__name__)


def analyze_costs(
    cost_data: dict,
    *,
    provider: str | None = None,
    model: str | None = None,
    client: LLMClient | None = None,
) -> str:
    """
    Send cost data to the configured LLM and return its analysis as markdown.

    Args:
        cost_data: Output of cost_explorer.get_full_analysis(), with
            optional 'waste_findings' from the pattern scan.
        provider: Override the configured llm_backend (legacy).
        model: Override the model ID.
        client: Inject a pre-built LLMClient (used by tests).
    """
    cost_text = _format_cost_data_for_llm(cost_data)
    template = load_template("cost_analysis")
    prompt = template.text + "\n\n" + cost_text

    if client is None:
        client = _client_from_args(provider=provider, model=model)

    response = client.complete(
        [Message(role="user", content=prompt)],
        model=model,
    )
    return response.text


def _client_from_args(*, provider: str | None, model: str | None) -> LLMClient:
    """Build an LLMClient from CLI/env args, going through WhisperConfig.

    Legacy callers pass provider='bedrock'|'openai'; that maps onto
    llm_backend in the config layer. CLI overrides win per principle 9.
    """
    from config import load_config

    overrides: dict[str, object] = {}
    if provider is not None:
        overrides["llm_backend"] = provider
    if model is not None:
        overrides["llm_model"] = model

    cfg = load_config(cli_overrides=overrides)
    return make_llm_client(cfg, prompt_template="cost_analysis")


def _format_cost_data_for_llm(cost_data: dict) -> str:
    """Convert structured cost data to readable text for the LLM."""
    lines: list[str] = []

    if "usage" in cost_data:
        usage = cost_data["usage"]
        lines.append(f"## Current Period: {usage['period']['start']} to {usage['period']['end']}")
        lines.append(f"**Total Cost: ${usage['total']:,.2f}**\n")

        lines.append("### Costs by Service:")
        for svc in usage.get("services", [])[:15]:
            lines.append(f"- {svc['name']}: ${svc['cost']:,.2f} ({svc['percent']}%)")
        lines.append("")

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

    if "regions" in cost_data:
        regions = cost_data["regions"]
        lines.append("### Costs by Region:")
        for region in regions.get("regions", [])[:10]:
            lines.append(f"- {region['name']}: ${region['cost']:,.2f} ({region['percent']}%)")
        lines.append("")

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
