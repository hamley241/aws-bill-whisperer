"""
Tests for BlockKitPresenter — Slack's native block format.

Slack imposes shape constraints (block type names, action_id presence,
50-block limit). These tests pin them down so we don't regress when
adding fields later.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from patterns.base import Finding, RiskTier
from presenters import BlockKitPresenter, ScanResult


def _finding(**overrides) -> Finding:
    defaults = dict(
        resource_id="vol-abc",
        resource_type="EBS Volume",
        region="us-east-1",
        monthly_impact_usd=42.5,
        summary="Delete unattached volume",
        pattern_id="001",
        risk_tier=RiskTier.HIGH,
        confidence=0.92,
        fix_command="aws ec2 delete-volume --volume-id vol-abc",
        safe_to_fix=True,
    )
    defaults.update(overrides)
    return Finding(**defaults)


class TestBlocksForFinding:
    def test_returns_list_of_blocks(self):
        blocks = BlockKitPresenter().blocks_for_finding(_finding())
        assert isinstance(blocks, list)
        assert all(isinstance(b, dict) and "type" in b for b in blocks)

    def test_first_block_is_section_with_summary(self):
        blocks = BlockKitPresenter().blocks_for_finding(_finding())
        assert blocks[0]["type"] == "section"
        text = blocks[0]["text"]["text"]
        assert "vol-abc" in text
        assert "$42.50" in text
        assert "high" in text.lower()
        assert "Delete unattached volume" in text

    def test_fix_command_in_code_block(self):
        blocks = BlockKitPresenter().blocks_for_finding(_finding())
        fix_blocks = [b for b in blocks
                      if b["type"] == "section"
                      and "Fix" in b["text"]["text"]]
        assert fix_blocks
        assert "```" in fix_blocks[0]["text"]["text"]

    def test_open_pr_button_when_fixable(self):
        blocks = BlockKitPresenter().blocks_for_finding(_finding())
        actions_blocks = [b for b in blocks if b["type"] == "actions"]
        assert actions_blocks
        button = actions_blocks[0]["elements"][0]
        assert button["action_id"] == "open_pr"
        assert button["text"]["text"] == "Open PR"

    def test_no_button_without_fix(self):
        finding = _finding(fix_command=None, fix_pr=None)
        blocks = BlockKitPresenter().blocks_for_finding(finding)
        assert not any(b["type"] == "actions" for b in blocks)

    def test_verbose_appends_evidence_context(self):
        finding = _finding(evidence={"size_gb": 100, "age_days": 47})
        blocks = BlockKitPresenter().blocks_for_finding(finding, verbose=True)
        ctx_blocks = [b for b in blocks if b["type"] == "context"]
        assert ctx_blocks
        assert "size_gb" in ctx_blocks[0]["elements"][0]["text"]


class TestBlocksForScan:
    def test_header_and_totals(self):
        result = ScanResult.from_findings([_finding()])
        blocks = BlockKitPresenter().blocks_for_scan(result)
        assert blocks[0]["type"] == "header"
        assert "Scan Results" in blocks[0]["text"]["text"]
        context = blocks[1]
        assert context["type"] == "context"
        assert "1" in context["elements"][0]["text"]
        assert "$42.50" in context["elements"][0]["text"]

    def test_empty_scan_renders_clean_message(self):
        result = ScanResult.from_findings([])
        blocks = BlockKitPresenter().blocks_for_scan(result)
        text_blob = json.dumps(blocks)
        assert "No issues found" in text_blob

    def test_inline_limit_with_overflow(self):
        findings = [
            _finding(resource_id=f"vol-{i}", monthly_impact_usd=float(10 + i))
            for i in range(7)
        ]
        result = ScanResult.from_findings(findings)
        blocks = BlockKitPresenter(inline_limit=3).blocks_for_scan(result)

        text_blob = json.dumps(blocks)
        # Top 3 visible (highest impact: 16, 15, 14)
        assert "vol-6" in text_blob and "vol-5" in text_blob and "vol-4" in text_blob
        # The other 4 are hidden behind overflow
        assert "4 more finding(s)" in text_blob
        overflow = [
            b for b in blocks
            if b.get("accessory", {}).get("action_id") == "scan_overflow"
        ]
        assert overflow

    def test_findings_sorted_by_impact_desc(self):
        findings = [
            _finding(resource_id="cheap", monthly_impact_usd=5.0),
            _finding(resource_id="expensive", monthly_impact_usd=500.0),
            _finding(resource_id="medium", monthly_impact_usd=50.0),
        ]
        result = ScanResult.from_findings(findings)
        blocks = BlockKitPresenter().blocks_for_scan(result)

        # Extract resource IDs in their block order
        text_blob = "\n".join(
            b.get("text", {}).get("text", "") if b["type"] == "section" else ""
            for b in blocks
        )
        first_expensive = text_blob.find("expensive")
        first_medium = text_blob.find("medium")
        first_cheap = text_blob.find("cheap")
        assert -1 < first_expensive < first_medium < first_cheap

    def test_under_slack_50_block_limit(self):
        """Slack rejects messages with >50 blocks."""
        findings = [_finding(resource_id=f"r-{i}") for i in range(20)]
        result = ScanResult.from_findings(findings)
        blocks = BlockKitPresenter().blocks_for_scan(result)
        assert len(blocks) <= 50

    def test_analysis_renders_as_section(self):
        result = ScanResult.from_findings(
            [_finding()],
            analysis="LLM summary: spend up because EC2.",
        )
        blocks = BlockKitPresenter().blocks_for_scan(result)
        text_blob = json.dumps(blocks)
        assert "LLM summary" in text_blob


class TestRenderSerialization:
    """The FindingPresenter contract (render_finding/render_scan returning str)."""

    def test_render_finding_is_valid_json(self):
        out = BlockKitPresenter().render_finding(_finding())
        parsed = json.loads(out)
        assert isinstance(parsed, list)

    def test_render_scan_is_valid_json(self):
        result = ScanResult.from_findings([_finding()])
        out = BlockKitPresenter().render_scan(result)
        parsed = json.loads(out)
        assert isinstance(parsed, list)
