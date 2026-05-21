"""Markdown presenter — fits both web UI and chat-bot textual output."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import FindingPresenter, ScanResult

if TYPE_CHECKING:
    from patterns.base import Finding


_RISK_BADGE = {
    "low": "🟢",
    "medium": "🟡",
    "high": "🔴",
}


class MarkdownPresenter(FindingPresenter):
    """GitHub-flavored markdown. Safe to paste into a chat, README, or PR body."""

    def render_finding(self, finding: "Finding", *, verbose: bool = False) -> str:
        badge = _RISK_BADGE.get(finding.risk_tier.value, "⚪️")
        header = (
            f"### {badge} {finding.resource_type} `{finding.resource_id}` "
            f"— ${finding.monthly_impact_usd:.2f}/mo"
        )
        meta = (
            f"_Region: `{finding.region}`  ·  "
            f"Risk: **{finding.risk_tier.value}**  ·  "
            f"Confidence: {finding.confidence:.0%}_"
        )
        lines = [header, "", meta, "", finding.summary]
        if finding.explanation:
            lines.extend(["", finding.explanation])
        if finding.fix_command:
            lines.extend(["", "**Fix:**", "```bash", finding.fix_command, "```"])
        if verbose and finding.evidence:
            lines.extend(["", "<details><summary>Evidence</summary>", "", "```json"])
            import json
            lines.append(json.dumps(finding.evidence, indent=2, default=str))
            lines.extend(["```", "</details>"])
        return "\n".join(lines)

    def render_scan(self, result: ScanResult, *, verbose: bool = False) -> str:
        lines = [
            "# AWS Bill Whisperer — Scan Results",
            "",
            f"**Findings:** {result.finding_count}  ·  "
            f"**Monthly waste:** ${result.total_monthly_impact_usd:.2f}  ·  "
            f"**Annual:** ${result.total_monthly_impact_usd * 12:.2f}",
        ]
        if result.metadata.get("account_id"):
            lines.append(f"_Account: `{result.metadata['account_id']}`_")
        lines.append("")

        if result.analysis:
            lines.extend(["## Summary", "", result.analysis, ""])

        if not result.findings:
            lines.append("✅ No issues found.")
            return "\n".join(lines)

        lines.append("## Findings")
        lines.append("")
        for f in result.sorted_by_impact():
            lines.append(self.render_finding(f, verbose=verbose))
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
