"""
Tests for src/presenters/ — the surface-agnostic Finding renderers.

Principle 3 contract: every output surface renders the same Finding
through a presenter; surfaces share zero rendering logic.
Principle 10: a test asserts CLI, web, Slack render the same finding
consistently — here we cover text/markdown/json. Block Kit lands in PR 2.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from patterns.base import Finding, RiskTier
from presenters import (
    FindingPresenter,
    JSONPresenter,
    MarkdownPresenter,
    ScanResult,
    TextPresenter,
)


def _sample_finding(**overrides) -> Finding:
    defaults = dict(
        resource_id="vol-abc",
        resource_type="EBS Volume",
        region="us-east-1",
        monthly_impact_usd=42.5,
        summary="Delete unattached volume",
        pattern_id="001",
        risk_tier=RiskTier.HIGH,
        confidence=0.92,
        fix_command="aws ec2 delete-volume --volume-id vol-abc --region us-east-1",
        evidence={"size_gb": 100, "age_days": 47},
        safe_to_fix=True,
    )
    defaults.update(overrides)
    return Finding(**defaults)


def _sample_result(findings: list[Finding] | None = None, **kwargs) -> ScanResult:
    return ScanResult.from_findings(findings or [_sample_finding()], **kwargs)


# ---------------------------------------------------------------------------
# ScanResult aggregate
# ---------------------------------------------------------------------------

class TestScanResult:
    def test_aggregates_total_impact(self):
        result = _sample_result([
            _sample_finding(resource_id="a", monthly_impact_usd=10.0),
            _sample_finding(resource_id="b", monthly_impact_usd=15.5),
        ])
        assert result.total_monthly_impact_usd == pytest.approx(25.5)
        assert result.finding_count == 2

    def test_sorted_by_impact_desc(self):
        result = _sample_result([
            _sample_finding(resource_id="cheap", monthly_impact_usd=1.0),
            _sample_finding(resource_id="big", monthly_impact_usd=500.0),
            _sample_finding(resource_id="mid", monthly_impact_usd=50.0),
        ])
        ids = [f.resource_id for f in result.sorted_by_impact()]
        assert ids == ["big", "mid", "cheap"]

    def test_groups_by_pattern(self):
        result = _sample_result([
            _sample_finding(resource_id="a", pattern_id="001"),
            _sample_finding(resource_id="b", pattern_id="004"),
            _sample_finding(resource_id="c", pattern_id="001"),
        ])
        groups = result.by_pattern()
        assert set(groups) == {"001", "004"}
        assert {f.resource_id for f in groups["001"]} == {"a", "c"}


# ---------------------------------------------------------------------------
# Per-presenter behaviour
# ---------------------------------------------------------------------------

class TestTextPresenter:
    def test_renders_compact_finding(self):
        out = TextPresenter().render_finding(_sample_finding())
        assert "vol-abc" in out
        assert "$42.50" in out
        assert "us-east-1" in out
        assert "HIGH" in out
        assert "92%" in out
        assert "aws ec2 delete-volume" in out

    def test_verbose_includes_evidence(self):
        f = _sample_finding(evidence={"size_gb": 100, "age_days": 47})
        out = TextPresenter().render_finding(f, verbose=True)
        assert "Evidence" in out
        assert "100" in out  # size_gb

    def test_scan_header_and_totals(self):
        result = _sample_result()
        out = TextPresenter().render_scan(result)
        assert "AWS Bill Whisperer" in out
        assert "Findings: 1" in out
        assert "$42.50" in out
        assert f"${42.5 * 12:.2f}" in out  # annual

    def test_empty_scan_renders_no_issues(self):
        result = ScanResult.from_findings([])
        out = TextPresenter().render_scan(result)
        assert "No issues found" in out

    def test_groups_findings_by_pattern_id(self):
        result = _sample_result([
            _sample_finding(resource_id="a", pattern_id="001"),
            _sample_finding(resource_id="b", pattern_id="004"),
        ])
        out = TextPresenter().render_scan(result)
        assert "Pattern 001" in out
        assert "Pattern 004" in out


class TestMarkdownPresenter:
    def test_finding_is_valid_markdown(self):
        out = MarkdownPresenter().render_finding(_sample_finding())
        assert out.startswith("### ")
        assert "`vol-abc`" in out
        assert "$42.50/mo" in out
        assert "```bash" in out  # fix command fenced

    def test_risk_badge_present(self):
        for tier, badge in [(RiskTier.LOW, "🟢"), (RiskTier.MEDIUM, "🟡"), (RiskTier.HIGH, "🔴")]:
            out = MarkdownPresenter().render_finding(_sample_finding(risk_tier=tier))
            assert badge in out

    def test_scan_has_summary_section_when_analysis_present(self):
        result = _sample_result(analysis="LLM said: spend went up because EC2.")
        out = MarkdownPresenter().render_scan(result)
        assert "## Summary" in out
        assert "LLM said" in out
        assert "## Findings" in out

    def test_verbose_evidence_collapsible(self):
        out = MarkdownPresenter().render_finding(_sample_finding(), verbose=True)
        assert "<details>" in out
        assert "</details>" in out


class TestJSONPresenter:
    def test_finding_round_trips(self):
        finding = _sample_finding()
        out = JSONPresenter().render_finding(finding)
        parsed = json.loads(out)
        assert parsed["resource_id"] == finding.resource_id
        assert parsed["monthly_impact_usd"] == 42.5
        assert parsed["risk_tier"] == "high"
        assert parsed["schema_version"] == "1"

    def test_scan_envelope(self):
        result = _sample_result()
        parsed = json.loads(JSONPresenter().render_scan(result))
        assert parsed["finding_count"] == 1
        assert parsed["total_monthly_impact_usd"] == 42.5
        assert parsed["annual_impact_usd"] == 510.0
        assert isinstance(parsed["findings"], list)
        assert parsed["findings"][0]["resource_id"] == "vol-abc"

    def test_no_legacy_field_names(self):
        result = _sample_result()
        parsed = json.loads(JSONPresenter().render_scan(result))
        flat = json.dumps(parsed)
        for legacy in ("monthly_cost", "recommendation", "severity"):
            assert f'"{legacy}"' not in flat


# ---------------------------------------------------------------------------
# Cross-surface consistency
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("presenter_cls", [TextPresenter, MarkdownPresenter, JSONPresenter])
class TestCrossSurfaceConsistency:
    """Every presenter must surface the same key facts about every Finding.

    This is the principle-3 + principle-10 contract: surfaces share a Finding,
    not a rendering. Each presenter chooses how, but must not lose the data.
    """

    def test_resource_id_appears(self, presenter_cls: type[FindingPresenter]):
        out = presenter_cls().render_finding(_sample_finding())
        assert "vol-abc" in out

    def test_dollar_value_appears(self, presenter_cls: type[FindingPresenter]):
        out = presenter_cls().render_finding(_sample_finding(monthly_impact_usd=123.4))
        # JSON has it numeric, others as $123.40
        assert "123.4" in out or "$123.40" in out

    def test_risk_tier_appears(self, presenter_cls: type[FindingPresenter]):
        out = presenter_cls().render_finding(_sample_finding(risk_tier=RiskTier.HIGH))
        assert "high" in out.lower()

    def test_scan_total_consistent(self, presenter_cls: type[FindingPresenter]):
        result = _sample_result([
            _sample_finding(resource_id="a", monthly_impact_usd=100.0),
            _sample_finding(resource_id="b", monthly_impact_usd=50.0),
        ])
        out = presenter_cls().render_scan(result)
        # All surfaces show the $150 total in some form
        assert "150" in out
