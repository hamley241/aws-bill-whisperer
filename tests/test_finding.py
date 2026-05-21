"""
Tests for the Finding dataclass — the universal currency of the system.
See CLAUDE.md principle 2 for the schema contract.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from patterns.base import Finding, RiskTier, SCHEMA_VERSION


class TestFindingSchema:
    """Schema contract: every field promised in CLAUDE.md principle 2 must exist."""

    def _minimal_finding(self) -> Finding:
        return Finding(
            resource_id="vol-abc",
            resource_type="EBS Volume",
            region="us-east-1",
            monthly_impact_usd=12.5,
            summary="Delete unattached volume",
        )

    def test_required_fields_accepted(self):
        f = self._minimal_finding()
        assert f.resource_id == "vol-abc"
        assert f.monthly_impact_usd == 12.5
        assert f.summary == "Delete unattached volume"

    def test_id_is_auto_generated_uuid(self):
        f1 = self._minimal_finding()
        f2 = self._minimal_finding()
        assert f1.id and f2.id
        assert f1.id != f2.id
        assert len(f1.id) == 36  # UUID4 string length

    def test_schema_version_is_set(self):
        f = self._minimal_finding()
        assert f.schema_version == SCHEMA_VERSION == "1"

    def test_defaults_for_optional_fields(self):
        f = self._minimal_finding()
        assert f.pattern_id == ""
        assert f.resource_arn is None
        assert f.account_id is None
        assert f.risk_tier == RiskTier.MEDIUM
        assert f.confidence == 0.8
        assert f.explanation is None
        assert f.fix_command is None
        assert f.fix_pr is None
        assert f.evidence == {}
        assert f.metadata == {}
        assert f.safe_to_fix is False

    def test_to_dict_uses_canonical_schema_names(self):
        f = Finding(
            resource_id="vol-abc",
            resource_type="EBS Volume",
            region="us-east-1",
            monthly_impact_usd=12.5,
            summary="Delete unattached volume",
            pattern_id="001",
            risk_tier=RiskTier.HIGH,
            confidence=0.95,
            account_id="123456789012",
            resource_arn="arn:aws:ec2:us-east-1:123456789012:volume/vol-abc",
            evidence={"size_gb": 100, "age_days": 30},
            fix_command="aws ec2 delete-volume --volume-id vol-abc",
        )
        d = f.to_dict()

        # All principle-2 fields present under their canonical names
        for key in (
            "id", "schema_version", "pattern_id", "resource_arn", "account_id",
            "region", "monthly_impact_usd", "confidence", "risk_tier", "summary",
            "explanation", "fix_command", "fix_pr", "evidence", "metadata",
        ):
            assert key in d, f"missing canonical field: {key}"

        # No legacy names leak through
        for legacy in ("monthly_cost", "recommendation", "severity"):
            assert legacy not in d, f"legacy field still serialized: {legacy}"

        assert d["risk_tier"] == "high"
        assert d["confidence"] == 0.95
        assert d["monthly_impact_usd"] == 12.5

    def test_monetary_value_is_rounded(self):
        f = Finding(
            resource_id="x", resource_type="y", region="us-east-1",
            monthly_impact_usd=12.345678, summary="s",
        )
        assert f.to_dict()["monthly_impact_usd"] == 12.35


class TestRiskTier:
    """Risk tier collapses the old 4-value Severity enum into 3 values."""

    def test_three_tiers(self):
        assert {t.value for t in RiskTier} == {"low", "medium", "high"}

    def test_no_critical_tier(self):
        assert not hasattr(RiskTier, "CRITICAL")
