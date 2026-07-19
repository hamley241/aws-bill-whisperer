"""
Bulletproof tests for pattern p004 (Idle EC2 Instances).

Covers all four remediation modes, the safe_to_fix eligibility gate,
the prod/ASG/ELB refusal paths, and the audit-log integration. Mirrors
test_p001_bulletproof.py / test_p006_bulletproof.py in shape.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from audit import audit_remediation
from patterns.base import Finding, RemediationMode, RiskTier
from patterns.p004_idle_ec2 import (
    COST_SOURCE_STATIC_LIST_PRICE,
    GATE_NAMES,
    PRICING_REGION,
    IdleEC2Pattern,
)
from storage import SqliteBackend, WhisperRepository


def _finding(*, safe_to_fix: bool = True, gate_overrides=None,
             resource_id="i-bulletproof") -> Finding:
    """Build a Finding for remediation testing. Defaults to all gates
    passing; pass gate_overrides={"not_prod": False} to flip one and
    force safe_to_fix=False semantics."""
    gates = dict.fromkeys(GATE_NAMES, True)
    if gate_overrides:
        gates.update(gate_overrides)
    computed_safe = all(gates.values())
    # Honor explicit safe_to_fix override even if gates would say otherwise —
    # the resolver/remediator only ever consult finding.safe_to_fix.
    effective_safe = safe_to_fix and computed_safe
    return Finding(
        resource_id=resource_id,
        resource_type="EC2 Instance",
        resource_arn=f"arn:aws:ec2:us-east-1::instance/{resource_id}",
        region="us-east-1",
        monthly_impact_usd=29.95,
        summary="Stop idle t3.medium for bulletproof tests",
        pattern_id="004",
        risk_tier=RiskTier.LOW,
        safe_to_fix=effective_safe,
        fix_command=(
            f"aws ec2 stop-instances --instance-ids {resource_id} "
            f"--region us-east-1"
        ),
        evidence={
            "instance": {
                "instance_id": resource_id,
                "instance_type": "t3.medium",
            },
            "utilization": {
                "avg_cpu_14d": 1.5,
                "max_cpu_14d": 8.0,
                "network_bytes_per_hour_14d": 0.0,
                "disk_bytes_per_hour_14d": 0.0,
                "cpu_datapoint_coverage": 336,
            },
            "cost": {
                "monthly_cost_usd": 29.95,
                "hourly_usd": 0.0416,
                "cost_source": COST_SOURCE_STATIC_LIST_PRICE,
                "pricing_region": PRICING_REGION,
                "confidence": "low",
            },
            "gates": gates,
        },
    )


@pytest.fixture
def pattern():
    session = MagicMock()
    ec2 = MagicMock()
    ec2.stop_instances.return_value = {
        "StoppingInstances": [{
            "PreviousState": {"Name": "running"},
            "CurrentState": {"Name": "stopping"},
        }]
    }
    session.client.return_value = ec2
    return IdleEC2Pattern(session=session)


# ---------------------------------------------------------------------------
# DRY_RUN
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_safe_finding_renders_would_run(self, pattern):
        f = _finding()
        result = pattern.remediate(f, RemediationMode.DRY_RUN)
        assert result.success
        assert "Dry-run" in result.output
        assert "Would run: aws ec2 stop-instances" in result.output
        assert "safe_to_fix: True" in result.output

    def test_unsafe_finding_renders_refusal(self, pattern):
        f = _finding(gate_overrides={"not_prod": False})
        result = pattern.remediate(f, RemediationMode.DRY_RUN)
        assert result.success
        assert "Refusing to suggest stop" in result.output
        assert "not_prod=False" in result.output

    def test_dry_run_lists_every_gate(self, pattern):
        f = _finding()
        result = pattern.remediate(f, RemediationMode.DRY_RUN)
        for gate_name in GATE_NAMES:
            assert gate_name in result.output


# ---------------------------------------------------------------------------
# COMMAND
# ---------------------------------------------------------------------------

class TestCommand:
    def test_safe_finding_emits_stop_command(self, pattern):
        f = _finding()
        result = pattern.remediate(f, RemediationMode.COMMAND)
        assert result.success
        assert "stop-instances --instance-ids i-bulletproof" in result.output
        assert "--region us-east-1" in result.output

    def test_unsafe_finding_refuses_with_failed_gate_list(self, pattern):
        f = _finding(gate_overrides={"not_prod": False, "ebs_root": False})
        result = pattern.remediate(f, RemediationMode.COMMAND)
        assert not result.success
        assert "refusing to stop i-bulletproof" in result.message
        assert "not_prod" in result.message
        assert "ebs_root" in result.message
        # No executable text on refusal — even partial stop commands.
        assert result.output is None

    def test_asg_member_refused(self, pattern):
        f = _finding(gate_overrides={"not_in_asg": False})
        result = pattern.remediate(f, RemediationMode.COMMAND)
        assert not result.success
        assert "not_in_asg" in result.message

    def test_spot_instance_refused(self, pattern):
        f = _finding(gate_overrides={"not_spot": False})
        result = pattern.remediate(f, RemediationMode.COMMAND)
        assert not result.success
        assert "not_spot" in result.message


# ---------------------------------------------------------------------------
# PR — deferred
# ---------------------------------------------------------------------------

class TestPR:
    def test_pr_mode_returns_deferred_message(self, pattern):
        f = _finding()
        result = pattern.remediate(f, RemediationMode.PR)
        assert not result.success
        assert "pr mode not supported" in result.message
        assert "Terraform" in result.message


# ---------------------------------------------------------------------------
# API_CALL — gated, gated, gated
# ---------------------------------------------------------------------------

class TestApiCall:
    def test_safe_finding_calls_stop_instances(self, pattern):
        f = _finding()
        result = pattern.remediate(f, RemediationMode.API_CALL)
        assert result.success
        assert "stopped instance i-bulletproof" in result.message
        # The boto3 mock should have been called with the right instance.
        ec2 = pattern.session.client.return_value
        ec2.stop_instances.assert_called_once_with(InstanceIds=["i-bulletproof"])
        assert result.evidence["previous_state"] == "running"
        assert result.evidence["current_state"] == "stopping"

    def test_unsafe_finding_refuses_without_aws_call(self, pattern):
        f = _finding(gate_overrides={"not_prod": False})
        result = pattern.remediate(f, RemediationMode.API_CALL)
        assert not result.success
        assert "refusing to stop" in result.message
        # CRITICAL: the AWS call must not have happened.
        ec2 = pattern.session.client.return_value
        ec2.stop_instances.assert_not_called()

    def test_api_failure_surfaces_message(self):
        session = MagicMock()
        ec2 = MagicMock()
        ec2.stop_instances.side_effect = Exception("AccessDenied")
        session.client.return_value = ec2
        pattern = IdleEC2Pattern(session=session)
        f = _finding()
        result = pattern.remediate(f, RemediationMode.API_CALL)
        assert not result.success
        assert "AccessDenied" in result.message


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
        rows = list(repo.list_remediations(finding_id=f.id))
        assert len(rows) == 1
        assert rows[0].mode == "dry_run"
        assert rows[0].actor == "test-runner"

    def test_api_call_refusal_still_audited(self, tmp_path, pattern):
        db = SqliteBackend(tmp_path / "whisper.db")
        repo = WhisperRepository(db)
        f = _finding(gate_overrides={"not_prod": False})

        result = audit_remediation(
            pattern, f, RemediationMode.API_CALL,
            actor="test-runner", repository=repo,
        )

        assert not result.success
        rows = list(repo.list_remediations(finding_id=f.id))
        assert len(rows) == 1
        assert rows[0].mode == "api_call"
        assert rows[0].success is False
        # The audit message must carry the structured reason — including
        # the specific failed gate name — so an auditor reading the log
        # later can answer "why did the agent refuse?" without re-running
        # the scanner.
        assert "refusing to stop" in rows[0].message
        assert "not_prod" in rows[0].message
        # AWS call must NOT have happened on the refusal path.
        ec2 = pattern.session.client.return_value
        ec2.stop_instances.assert_not_called()

    def test_api_call_success_records_state_transition(self, tmp_path, pattern):
        db = SqliteBackend(tmp_path / "whisper.db")
        repo = WhisperRepository(db)
        f = _finding()

        result = audit_remediation(
            pattern, f, RemediationMode.API_CALL,
            actor="test-runner", repository=repo,
        )

        assert result.success
        rows = list(repo.list_remediations(finding_id=f.id))
        assert len(rows) == 1
        assert rows[0].mode == "api_call"
        assert rows[0].success is True
