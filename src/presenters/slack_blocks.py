"""
Block Kit presenter — Slack's native block format.

Implements FindingPresenter so it composes with the others under the
principle-3 contract. Slack-specific helpers (blocks_for_finding,
blocks_for_scan) return Python lists ready to hand to Bolt's `respond`
or `chat.postMessage`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ._slack_text import escape_mrkdwn
from .base import FindingPresenter, ScanResult

if TYPE_CHECKING:
    from patterns.base import Finding


_RISK_EMOJI = {
    "low": "🟢",
    "medium": "🟡",
    "high": "🔴",
}

# Slack limits a single message to 50 blocks (and ~3000 chars per text).
# Showing more than ~5 findings inline gets noisy; the rest hide behind
# the overflow menu — UI affordance + safe under Slack's limits.
DEFAULT_INLINE_LIMIT = 5


class BlockKitPresenter(FindingPresenter):
    """Slack Block Kit renderer."""

    def __init__(self, *, inline_limit: int = DEFAULT_INLINE_LIMIT):
        self._inline_limit = inline_limit

    # ------------------------------------------------------------------
    # FindingPresenter contract — string outputs (JSON of blocks).
    # ------------------------------------------------------------------

    def render_finding(self, finding: "Finding", *, verbose: bool = False) -> str:
        return json.dumps(self.blocks_for_finding(finding, verbose=verbose))

    def render_scan(self, result: ScanResult, *, verbose: bool = False) -> str:
        return json.dumps(self.blocks_for_scan(result, verbose=verbose))

    # ------------------------------------------------------------------
    # Slack-native helpers — return native lists ready for Bolt.
    # ------------------------------------------------------------------

    def blocks_for_finding(self, finding: "Finding", *,
                           verbose: bool = False) -> list[dict[str, Any]]:
        emoji = _RISK_EMOJI.get(finding.risk_tier.value, "⚪️")
        # resource_type/resource_id/region come from AWS but can carry
        # user-controlled tag content — escape defensively.
        title = (
            f"*{emoji} {escape_mrkdwn(finding.resource_type)} "
            f"`{escape_mrkdwn(finding.resource_id)}`* "
            f"— *${finding.monthly_impact_usd:.2f}/mo*"
        )
        meta = (
            f"_Region: `{escape_mrkdwn(finding.region)}` · "
            f"Risk: *{finding.risk_tier.value}* · "
            f"Confidence: {finding.confidence:.0%}_"
        )
        blocks: list[dict[str, Any]] = [
            {"type": "section",
             "text": {"type": "mrkdwn",
                      "text": f"{title}\n{meta}\n{escape_mrkdwn(finding.summary)}"}},
        ]
        if finding.explanation:
            # LLM-generated — highest-risk injection vector.
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": escape_mrkdwn(finding.explanation)},
            })
        if finding.fix_command:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": f"*Fix*\n```{escape_mrkdwn(finding.fix_command)}```"},
            })

        actions: list[dict[str, Any]] = []
        if finding.fix_pr or finding.fix_command:
            actions.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "Open PR"},
                "style": "primary",
                "value": finding.id,
                "action_id": "open_pr",
            })
        if actions:
            blocks.append({"type": "actions", "elements": actions})

        if verbose and finding.evidence:
            blocks.append({
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": f"_Evidence:_ `{json.dumps(finding.evidence, default=str)}`",
                }],
            })
        return blocks

    def blocks_for_scan(self, result: ScanResult, *,
                        verbose: bool = False) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = [
            {"type": "header",
             "text": {"type": "plain_text",
                      "text": "AWS Bill Whisperer — Scan Results"}},
            {"type": "context",
             "elements": [{
                 "type": "mrkdwn",
                 "text": (
                     f":mag: *{result.finding_count}* findings  ·  "
                     f":moneybag: *${result.total_monthly_impact_usd:.2f}/mo*  ·  "
                     f"~${result.total_monthly_impact_usd * 12:.2f}/yr"
                 ),
             }]},
        ]

        if result.analysis:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                # analysis is the LLM narrative — escape angle brackets so
                # a prompt-injected narrative can't fake mentions, broadcasts,
                # or deceptive links inside a shared Slack channel.
                "text": {"type": "mrkdwn", "text": escape_mrkdwn(result.analysis)},
            })

        if not result.findings:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": ":white_check_mark: *No issues found.* "
                                 "Your AWS bill is clean."},
            })
            return blocks

        ranked = result.sorted_by_impact()
        shown = ranked[: self._inline_limit]
        hidden = ranked[self._inline_limit:]

        for finding in shown:
            blocks.append({"type": "divider"})
            blocks.extend(self.blocks_for_finding(finding, verbose=verbose))

        if hidden:
            hidden_total = sum(f.monthly_impact_usd for f in hidden)
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"_{len(hidden)} more finding(s) totaling "
                        f"${hidden_total:.2f}/mo not shown._"
                    ),
                },
                "accessory": {
                    "type": "overflow",
                    "action_id": "scan_overflow",
                    "options": [
                        {"text": {"type": "plain_text", "text": "Show all findings"},
                         "value": "show_all"},
                        {"text": {"type": "plain_text", "text": "Download JSON"},
                         "value": "download_json"},
                    ],
                },
            })
        return blocks
