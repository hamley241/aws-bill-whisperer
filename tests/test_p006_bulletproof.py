"""
Bulletproof tests for pattern p006 (NAT Gateway Optimization).

Covers all four remediation modes, the candidate-tier gating on
COMMAND, and the audit-log integration. Mirrors test_p001_bulletproof.py
in shape.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from audit import audit_remediation
from patterns.base import Finding, RemediationMode, RiskTier
from patterns.p006_nat_gateway import (
    EVIDENCE_TIER_INFERRED,
    EVIDENCE_TIER_OBSERVED,
    NatGatewayPattern,
)
from storage import SqliteBackend, WhisperRepository


def _finding(*, candidates=None, monthly_impact_usd=32.4) -> Finding:
    candidates = candidates if candidates is not None else [
        {
            "candidate_id": "cand-gateway-s3",
            "service": "s3",
            "endpoint_type": "Gateway",
            "evidence_tier": EVIDENCE_TIER_INFERRED,
            "supporting_observed_share": None,
            "supporting_inference_reason": "service_endpoint_supported_by_aws",
            "est_monthly_savings_usd": 0.0,
            "blast_radius": "low",
            "deterministic_command_hint":
                "aws ec2 create-vpc-endpoint --service-name com.amazonaws.us-east-1.s3 "
                "--vpc-id <vpc-id> --route-table-ids <rtb-ids> --region us-east-1",
        },
    ]
    return Finding(
        resource_id="nat-bulletproof",
        resource_type="NAT Gateway",
        resource_arn="arn:aws:ec2:us-east-1::natgateway/nat-bulletproof",
        region="us-east-1",
        monthly_impact_usd=monthly_impact_usd,
        summary="NAT for bulletproof tests",
        pattern_id="006",
        risk_tier=RiskTier.MEDIUM,
        safe_to_fix=False,
        evidence={
            "cost": {"cost_source": "hourly_only"},
            "inferred": {"endpoint_candidates": candidates},
        },
    )


@pytest.fixture
def pattern():
    return NatGatewayPattern(session=MagicMock())


# ---------------------------------------------------------------------------
# DRY_RUN
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_succeeds_and_lists_candidates(self, pattern):
        f = _finding()
        result = pattern.remediate(f, RemediationMode.DRY_RUN)

        assert result.success
        assert "Dry-run" in result.output
        assert "cand-gateway-s3" in result.output
        assert "s3 Gateway" in result.output
        assert "hourly_only" in result.output

    def test_dry_run_handles_no_candidates(self, pattern):
        f = _finding(candidates=[])
        result = pattern.remediate(f, RemediationMode.DRY_RUN)

        assert result.success
        assert "no candidates" in result.output


# ---------------------------------------------------------------------------
# COMMAND
# ---------------------------------------------------------------------------

class TestCommand:
    def test_command_refuses_when_only_inferred_candidates(self, pattern):
        f = _finding()  # default candidate is inferred
        result = pattern.remediate(f, RemediationMode.COMMAND)

        assert not result.success
        assert result.message == "insufficient_evidence_for_command"
        assert result.output is None

    def test_command_emits_aws_command_for_observed_candidate(self, pattern):
        observed = [{
            "candidate_id": "cand-gateway-s3",
            "service": "s3",
            "endpoint_type": "Gateway",
            "evidence_tier": EVIDENCE_TIER_OBSERVED,
            "supporting_observed_share": 0.66,
            "supporting_inference_reason": None,
            "est_monthly_savings_usd": 271.0,
            "blast_radius": "low",
            "deterministic_command_hint":
                "aws ec2 create-vpc-endpoint --service-name com.amazonaws.us-east-1.s3 "
                "--vpc-id vpc-1 --route-table-ids rtb-1 --region us-east-1",
        }]
        f = _finding(candidates=observed, monthly_impact_usd=412.0)

        result = pattern.remediate(f, RemediationMode.COMMAND)

        assert result.success
        assert "create-vpc-endpoint" in result.output
        assert "com.amazonaws.us-east-1.s3" in result.output
        assert result.evidence["candidate_id"] == "cand-gateway-s3"


# ---------------------------------------------------------------------------
# PR — deferred
# ---------------------------------------------------------------------------

class TestPR:
    def test_pr_mode_returns_deferred_message(self, pattern):
        f = _finding()
        result = pattern.remediate(f, RemediationMode.PR)

        assert not result.success
        assert "pr mode not supported" in result.message
        assert "deferred" in result.message


# ---------------------------------------------------------------------------
# API_CALL — forbidden in OSS this milestone
# ---------------------------------------------------------------------------

class TestApiCall:
    def test_api_call_is_forbidden(self, pattern):
        f = _finding()
        result = pattern.remediate(f, RemediationMode.API_CALL)

        assert not result.success
        assert result.message == "not_supported_in_oss_milestone"


# ---------------------------------------------------------------------------
# Audit log integration — every remediate() call lands in the repository
# ---------------------------------------------------------------------------

class TestAuditIntegration:
    def test_dry_run_writes_to_audit_log(self, tmp_path, pattern):
        db = SqliteBackend(tmp_path / "whisper.db")
        repo = WhisperRepository(db)
        f = _finding()

        result = audit_remediation(
            pattern, f, RemediationMode.DRY_RUN,
            actor="test-runner", repository=repo,
        )

        assert result.success
        # Repository should now show one remediation row for this finding.
        rows = list(repo.list_remediations(finding_id=f.id))
        assert len(rows) == 1
        assert rows[0].mode == "dry_run"
        assert rows[0].actor == "test-runner"

    def test_api_call_failure_still_audited(self, tmp_path, pattern):
        db = SqliteBackend(tmp_path / "whisper.db")
        repo = WhisperRepository(db)
        f = _finding()

        result = audit_remediation(
            pattern, f, RemediationMode.API_CALL,
            actor="test-runner", repository=repo,
        )

        assert not result.success
        rows = list(repo.list_remediations(finding_id=f.id))
        assert len(rows) == 1
        assert rows[0].mode == "api_call"
        assert rows[0].success is False
        assert "not_supported_in_oss_milestone" in rows[0].message
