"""
Per-finding LLM explanation step.

Populates Finding.explanation for the highest-impact findings using the
customer's configured LLMClient. Top-N selection caps LLM spend; the
rest of the findings rely on their machine-generated summary.

This step is best-effort: if the LLM fails or isn't configured, the
findings still render — they just don't get the long-form explanation.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

_SRC = Path(__file__).parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from llm import LLMClient, Message, make_llm_client  # noqa: E402
from prompts import load_template  # noqa: E402

if TYPE_CHECKING:
    from config import WhisperConfig
    from patterns.base import Finding


logger = logging.getLogger(__name__)


DEFAULT_TOP_N = 5
TEMPLATE_NAME = "finding_explanation"


def explain_findings(
    findings: list["Finding"],
    *,
    client: LLMClient | None = None,
    config: "WhisperConfig | None" = None,
    top_n: int = DEFAULT_TOP_N,
) -> None:
    """Mutate the top-N findings in place, setting `explanation`.

    `client` overrides config; tests pass a stub.
    Already-explained findings (`explanation` already set) are skipped
    so this is safe to re-run.
    """
    if not findings:
        return

    if client is None:
        if config is None:
            logger.info("no LLMClient and no config — skipping explanations")
            return
        try:
            client = make_llm_client(config, prompt_template=TEMPLATE_NAME)
        except ValueError as e:
            logger.warning("LLM not configured (%s) — skipping explanations", e)
            return

    template = load_template(TEMPLATE_NAME)

    targets = _top_n_for_explanation(findings, top_n)
    for finding in targets:
        if finding.explanation:
            continue
        try:
            finding.explanation = _explain_one(finding, client, template.text)
        except Exception as e:
            logger.warning(
                "explanation failed for finding %s: %s",
                finding.resource_id, e,
            )


def _top_n_for_explanation(findings: list["Finding"], top_n: int) -> list["Finding"]:
    return sorted(
        findings, key=lambda f: f.monthly_impact_usd, reverse=True
    )[:top_n]


def _explain_one(finding: "Finding", client: LLMClient, template_text: str) -> str:
    evidence_json = json.dumps(finding.evidence, default=str) if finding.evidence else "{}"
    prompt = template_text.format(
        resource_type=finding.resource_type,
        region=finding.region,
        risk_tier=finding.risk_tier.value,
        summary=finding.summary,
        fix_command=finding.fix_command or "(none)",
        evidence_json=evidence_json,
    )
    response = client.complete(
        [Message(role="user", content=prompt)],
        max_tokens=256,
        temperature=0.2,
    )
    return response.text.strip()
