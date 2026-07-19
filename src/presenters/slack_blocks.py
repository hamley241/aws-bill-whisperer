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

from ._slack_text import (
    SLACK_MAX_MRKDWN_CHARS,
    escape_mrkdwn,
    safe_mrkdwn,
    safe_mrkdwn_code,
)
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

# Per-field length budgets for Slack mrkdwn elements on the scan surface.
# Each block's composed text must stay under SLACK_MAX_MRKDWN_CHARS.
# Budgets leave headroom for surrounding decorators (title, meta line,
# code-span backticks). LLM-generated explanation and analysis are the
# fields most likely to run long; evidence JSON can be huge and is
# clipped aggressively to keep verbose-mode output postable.
MAX_FINDING_SUMMARY_LEN = 1500
MAX_FINDING_EXPLANATION_LEN = 2400
MAX_FINDING_FIX_COMMAND_LEN = 1500
MAX_FINDING_EVIDENCE_LEN = 2400
MAX_FINDING_RESOURCE_ID_LEN = 200
MAX_FINDING_RESOURCE_TYPE_LEN = 100
MAX_FINDING_REGION_LEN = 50
MAX_RESULT_ANALYSIS_LEN = 2800


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
        # user-controlled tag content. resource_id and region live in
        # code spans → use the backtick-stripping variant.
        title = (
            f"*{emoji} {safe_mrkdwn(finding.resource_type, MAX_FINDING_RESOURCE_TYPE_LEN)} "
            f"`{safe_mrkdwn_code(finding.resource_id, MAX_FINDING_RESOURCE_ID_LEN)}`* "
            f"— *${finding.monthly_impact_usd:.2f}/mo*"
        )
        meta = (
            f"_Region: `{safe_mrkdwn_code(finding.region, MAX_FINDING_REGION_LEN)}` · "
            f"Risk: *{finding.risk_tier.value}* · "
            f"Confidence: {finding.confidence:.0%}_"
        )
        summary = safe_mrkdwn(finding.summary, MAX_FINDING_SUMMARY_LEN)
        blocks: list[dict[str, Any]] = [
            {"type": "section",
             "text": {"type": "mrkdwn",
                      "text": f"{title}\n{meta}\n{summary}"}},
        ]
        if finding.explanation:
            # LLM-generated — highest-risk injection vector.
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": safe_mrkdwn(finding.explanation,
                                             MAX_FINDING_EXPLANATION_LEN)},
            })
        if finding.fix_command:
            # Lives inside a triple-backtick fence → strip backticks
            # to prevent code-fence escape + clip to per-field budget.
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": (
                             "*Fix*\n```"
                             f"{safe_mrkdwn_code(finding.fix_command, MAX_FINDING_FIX_COMMAND_LEN)}"
                             "```"
                         )},
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
            # Evidence is scanner JSON that can contain user-controlled
            # strings (tag values, names). It lives inside a code span,
            # so:
            #   1. escape angle-brackets so any embedded `<@user>` /
            #      `<!channel>` / `<URL>` can't reintroduce the injection
            #      vector via the verbose path
            #   2. strip backticks so a tag value containing `\`` can't
            #      close the code span and let following content render
            #      as raw mrkdwn
            #   3. clip to a per-field budget — evidence dumps for
            #      patterns like p006 (Flow Logs samples) can easily
            #      exceed Slack's 3000-char per-text-element limit
            evidence_dump = json.dumps(finding.evidence, default=str)
            blocks.append({
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": (
                        "_Evidence:_ `"
                        f"{safe_mrkdwn_code(evidence_dump, MAX_FINDING_EVIDENCE_LEN)}"
                        "`"
                    ),
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
                # or deceptive links inside a shared Slack channel; clip to
                # per-field budget so a runaway narrative doesn't blow Slack's
                # per-text-element limit.
                "text": {"type": "mrkdwn",
                         "text": safe_mrkdwn(result.analysis, MAX_RESULT_ANALYSIS_LEN)},
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
