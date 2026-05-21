"""Terminal-friendly presenter — the CLI's primary output."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .base import FindingPresenter, ScanResult

if TYPE_CHECKING:
    from patterns.base import Finding


class TextPresenter(FindingPresenter):
    """Plain text, terminal-width, no ANSI codes (color is the caller's job)."""

    def render_finding(self, finding: "Finding", *, verbose: bool = False) -> str:
        lines = [
            f"  📍 {finding.resource_type}: {finding.resource_id}",
            f"     Region: {finding.region}",
            f"     Monthly Impact: ${finding.monthly_impact_usd:.2f}",
            f"     Risk: {finding.risk_tier.value.upper()}  (confidence {finding.confidence:.0%})",
            f"     Summary: {finding.summary}",
        ]
        if finding.safe_to_fix:
            lines.append("     ✅ Safe to auto-fix")
        else:
            lines.append("     ⚠️  Manual review required")
        if finding.fix_command:
            lines.append(f"     Fix: {finding.fix_command}")
        if finding.explanation:
            lines.append(f"     Why: {finding.explanation}")
        if verbose:
            if finding.evidence:
                lines.append(f"     Evidence: {json.dumps(finding.evidence, default=str)}")
            if finding.metadata:
                lines.append(f"     Metadata: {json.dumps(finding.metadata, default=str)}")
        return "\n".join(lines)

    def render_scan(self, result: ScanResult, *, verbose: bool = False) -> str:
        header = [
            "=" * 60,
            "AWS Bill Whisperer — Scan Results",
            "=" * 60,
        ]
        if result.metadata.get("account_id"):
            header.append(f"Account: {result.metadata['account_id']}")
        header.append(
            f"Findings: {result.finding_count}    "
            f"Monthly waste: ${result.total_monthly_impact_usd:.2f}    "
            f"Annual: ${result.total_monthly_impact_usd * 12:.2f}"
        )

        body: list[str] = []
        if result.analysis:
            body.extend(["", result.analysis, ""])

        if not result.findings:
            body.append("\n✅ No issues found.")
        else:
            grouped = result.by_pattern()
            for pattern_id in sorted(grouped):
                findings = grouped[pattern_id]
                pattern_total = sum(f.monthly_impact_usd for f in findings)
                body.append("")
                body.append(
                    f"🔹 Pattern {pattern_id}  "
                    f"({len(findings)} finding(s), ${pattern_total:.2f}/mo)"
                )
                for f in sorted(findings, key=lambda x: x.monthly_impact_usd, reverse=True):
                    body.append(self.render_finding(f, verbose=verbose))

        footer = [
            "",
            "=" * 60,
            f"TOTAL MONTHLY: ${result.total_monthly_impact_usd:.2f}    "
            f"ANNUAL: ${result.total_monthly_impact_usd * 12:.2f}",
            "=" * 60,
        ]
        return "\n".join(header + body + footer)
