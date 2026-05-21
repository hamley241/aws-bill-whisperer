"""
Thread-level Q&A with the LLM.

When a user replies in a Slack thread anchored to a recent scan, we
pack the scan as context, ask the configured LLM to answer the
question, and return plain text suitable for chat_postMessage.

Same LLMClient + prompt-logging contract as elsewhere (principle 5).
"""

from __future__ import annotations

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
    from presenters import ScanResult


logger = logging.getLogger(__name__)
TEMPLATE_NAME = "thread_reply"
MAX_FINDINGS_IN_CONTEXT = 10


def answer_thread_question(
    question: str,
    *,
    scan_result: "ScanResult | None",
    client: LLMClient | None = None,
    config: "WhisperConfig | None" = None,
) -> str:
    """Return a plain-text answer for posting back to the Slack thread.

    `client` overrides config. Returns a graceful fallback string if
    the LLM isn't configured or the call fails — never raises into
    the Slack handler.
    """
    if client is None:
        if config is None:
            return _no_llm_fallback(question)
        try:
            client = make_llm_client(config, prompt_template=TEMPLATE_NAME)
        except ValueError as e:
            logger.warning("LLM not configured (%s) — returning fallback", e)
            return _no_llm_fallback(question)

    template = load_template(TEMPLATE_NAME)
    context_text = _format_scan_for_context(scan_result)
    prompt = template.text.format(question=question, scan_context=context_text)

    try:
        response = client.complete(
            [Message(role="user", content=prompt)],
            max_tokens=512,
            temperature=0.3,
        )
    except Exception as e:
        logger.exception("thread reply LLM call failed")
        return f":x: I couldn't answer that right now ({e})."
    return response.text.strip()


def _format_scan_for_context(scan_result: "ScanResult | None") -> str:
    if scan_result is None or not scan_result.findings:
        return "(no recent scan in this thread)"

    lines = [
        f"Total monthly waste: ${scan_result.total_monthly_impact_usd:.2f} "
        f"across {scan_result.finding_count} findings.",
        "",
        "Top findings:",
    ]
    for finding in scan_result.sorted_by_impact()[:MAX_FINDINGS_IN_CONTEXT]:
        line = (
            f"- [{finding.pattern_id}] {finding.resource_type} "
            f"{finding.resource_id} in {finding.region}: "
            f"${finding.monthly_impact_usd:.2f}/mo — {finding.summary}"
        )
        if finding.fix_command:
            line += f" (fix: `{finding.fix_command}`)"
        lines.append(line)
    return "\n".join(lines)


def _no_llm_fallback(question: str) -> str:
    return (
        ":grey_question: I can't answer follow-up questions without an LLM "
        "configured. Run `whisper-config doctor` to see what's missing. "
        f"(Your question was: \"{question[:120]}\")"
    )
