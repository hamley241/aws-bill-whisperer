"""
Bulletproof tests for pattern p001 (Unattached EBS Volumes).

Exercises all four remediation modes, the safety gates, the Terraform
PR hint, and audit-log integration.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from audit import audit_remediation
from patterns.base import Finding, RemediationMode, RiskTier
from patterns.p001_unattached_ebs import (
    MIN_AGE_DAYS_FOR_AUTO_DELETE,
    MIN_SNAPSHOT_AGE_DAYS_BEFORE_DELETE,
    TERRAFORM_TAG_KEY,
    UnattachedEBSPattern,
)
from storage import SqliteBackend, WhisperRepository


def _finding(**overrides) -> Finding:
    defaults = dict(
        resource_id="vol-abc",
        resource_type="EBS Volume",
        region="us-east-1",
        monthly_impact_usd=10.0,
        summary="Delete unattached volume",
        pattern_id="001",
        risk_tier=RiskTier.MEDIUM,
        fix_command="aws ec2 delete-volume --volume-id vol-abc --region us-east-1",
        safe_to_fix=True,
        evidence={
            "size_gb": 100,
            "age_days": 30,
            "has_snapshot": True,
            "latest_snapshot_age_days": 5,
            "snapshot_count": 1,
            "terraform_managed": False,
        },
    )
    defaults.update(overrides)
    return Finding(**defaults)


@pytest.fixture
def pattern():
    return UnattachedEBSPattern(session=MagicMock())


# ---------------------------------------------------------------------------
# Scan-side enrichment
# ---------------------------------------------------------------------------

class TestScanEvidence:
    def _setup(self, *, vol_age_days=30, snap_age_days=5,
               tags=None, snapshots=None):
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2

        vol_time = datetime.now(timezone.utc) - timedelta(days=vol_age_days)
        snap_time = (
            datetime.now(timezone.utc) - timedelta(days=snap_age_days)
            if snap_age_days is not None else None
        )
        mock_ec2.describe_volumes.return_value = {
            "Volumes": [{
                "VolumeId": "vol-abc",
                "Size": 100,
                "VolumeType": "gp3",
                "CreateTime": vol_time,
                "Tags": tags or [],
            }]
        }
        if snapshots is None:
            snapshots = (
                [{"SnapshotId": "snap-1", "StartTime": snap_time}]
                if snap_age_days is not None else []
            )
        mock_ec2.describe_snapshots.return_value = {"Snapshots": snapshots}
        p = UnattachedEBSPattern(session=mock_session)
        p.get_all_regions = lambda: ["us-east-1"]
        return p

    def test_evidence_captures_snapshot_age(self):
        p = self._setup(snap_age_days=10)
        f = p.scan()[0]
        assert f.evidence["latest_snapshot_age_days"] == 10
        assert f.evidence["snapshot_count"] == 1

    def test_evidence_captures_terraform_tag(self):
        p = self._setup(tags=[{"Key": TERRAFORM_TAG_KEY, "Value": "true"}])
        f = p.scan()[0]
        assert f.evidence["terraform_managed"] is True

    def test_no_terraform_tag_is_explicit_false(self):
        p = self._setup(tags=[{"Key": "Env", "Value": "prod"}])
        f = p.scan()[0]
        assert f.evidence["terraform_managed"] is False

    def test_confidence_grows_with_age(self):
        young = self._setup(vol_age_days=2, snap_age_days=1).scan()[0]
        old = self._setup(vol_age_days=60, snap_age_days=10).scan()[0]
        assert young.confidence < old.confidence
        assert 0.0 < young.confidence <= 1.0
        assert 0.0 < old.confidence <= 1.0

    def test_resource_arn_populated(self):
        p = self._setup()
        f = p.scan()[0]
        assert f.resource_arn == "arn:aws:ec2:us-east-1::volume/vol-abc"

    def test_no_snapshot_blocks_safe_to_fix(self):
        p = self._setup(snap_age_days=None)
        f = p.scan()[0]
        assert f.evidence["has_snapshot"] is False
        assert f.safe_to_fix is False

    def test_recent_snapshot_blocks_safe_to_fix(self):
        # newest snapshot is 0 days old → unsafe (might still be pending)
        p = self._setup(snap_age_days=0)
        f = p.scan()[0]
        assert f.safe_to_fix is False


# ---------------------------------------------------------------------------
# Remediation modes
# ---------------------------------------------------------------------------

class TestRemediateModes:
    def test_dry_run(self, pattern):
        r = pattern.remediate(_finding(), RemediationMode.DRY_RUN)
        assert r.success
        assert r.mode == RemediationMode.DRY_RUN
        assert "would execute" in r.message

    def test_command(self, pattern):
        r = pattern.remediate(_finding(), RemediationMode.COMMAND)
        assert r.success
        assert r.output.startswith("aws ec2 delete-volume")

    def test_api_call_success(self):
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_session.client.return_value = mock_ec2
        p = UnattachedEBSPattern(session=mock_session)

        r = p.remediate(_finding(), RemediationMode.API_CALL)

        assert r.success
        mock_ec2.delete_volume.assert_called_once_with(VolumeId="vol-abc")
        assert "deleted" in r.message
        assert r.evidence["size_gb_recovered"] == 100

    def test_api_call_unsafe_finding(self, pattern):
        unsafe = _finding(
            safe_to_fix=False,
            evidence={
                "size_gb": 50, "age_days": 3, "has_snapshot": False,
                "latest_snapshot_age_days": None, "snapshot_count": 0,
                "terraform_managed": False,
            },
        )
        r = pattern.remediate(unsafe, RemediationMode.API_CALL)
        assert not r.success
        # The safety message lists every reason in plain English
        assert "no snapshot" in r.message
        assert "must be" in r.message  # the age threshold mention

    def test_api_call_aws_error(self):
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_ec2.delete_volume.side_effect = RuntimeError("AccessDenied")
        mock_session.client.return_value = mock_ec2
        p = UnattachedEBSPattern(session=mock_session)

        r = p.remediate(_finding(), RemediationMode.API_CALL)
        assert not r.success
        assert "AccessDenied" in r.message


class TestPRMode:
    def test_pr_for_terraform_managed_volume(self, pattern):
        f = _finding(evidence={**_finding().evidence, "terraform_managed": True})
        r = pattern.remediate(f, RemediationMode.PR)
        assert r.success
        assert r.mode == RemediationMode.PR
        assert "aws_ebs_volume" in r.output
        assert f.resource_id in r.output
        assert "terraform plan" in r.output

    def test_pr_refuses_untagged_volume(self, pattern):
        r = pattern.remediate(_finding(), RemediationMode.PR)
        assert not r.success
        assert TERRAFORM_TAG_KEY in r.message


# ---------------------------------------------------------------------------
# audit_remediation integration
# ---------------------------------------------------------------------------

class TestAuditRemediation:
    @pytest.fixture
    def repo(self, tmp_path):
        return WhisperRepository(backend=SqliteBackend(db_path=tmp_path / "a.db"))

    def test_writes_to_audit_log(self, pattern, repo):
        result = audit_remediation(
            pattern, _finding(), RemediationMode.DRY_RUN,
            actor="U-test", repository=repo,
        )
        rows = repo.list_remediations()
        assert len(rows) == 1
        assert rows[0].finding_id == result.finding_id
        assert rows[0].mode == "dry_run"
        assert rows[0].actor == "U-test"

    def test_audit_failures_do_not_swallow_result(self, pattern):
        bad_repo = MagicMock()
        bad_repo.record_remediation.side_effect = RuntimeError("disk full")
        # Even with a broken audit log, the caller still gets a result.
        result = audit_remediation(
            pattern, _finding(), RemediationMode.DRY_RUN,
            actor="U", repository=bad_repo,
        )
        assert result.success

    def test_audit_log_captures_each_attempt(self, pattern, repo):
        f = _finding()
        for mode in (RemediationMode.DRY_RUN, RemediationMode.COMMAND):
            audit_remediation(pattern, f, mode, repository=repo)
        rows = repo.list_remediations(finding_id=f.id)
        modes = sorted(r.mode for r in rows)
        assert modes == ["command", "dry_run"]


# ---------------------------------------------------------------------------
# Safety constants — these are policy, surface them as testable
# ---------------------------------------------------------------------------

class TestSafetyConstants:
    def test_age_threshold_nonzero(self):
        assert MIN_AGE_DAYS_FOR_AUTO_DELETE >= 1

    def test_snapshot_age_threshold_nonzero(self):
        assert MIN_SNAPSHOT_AGE_DAYS_BEFORE_DELETE >= 1
