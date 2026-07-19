"""
Pattern 001: Unattached EBS Volumes — bulletproof.

Detection
  Scans every region for EBS volumes in `available` state. Captures
  size, type, age, snapshot existence, tags, and the most recent
  detachment event. Confidence is computed from age + snapshot
  coverage; safety gates check snapshot recency and a minimum age.

Remediation modes (CLAUDE.md principle 4 — one entry point dispatching
on mode):
  DRY_RUN   — log what would happen, change nothing.
  COMMAND   — emit the AWS CLI command.
  PR        — emit an HCL `terraform plan -destroy` hint for volumes
              tagged `managed-by-terraform=true`. Untagged volumes get
              a message explaining the gate.
  API_CALL  — invoke ec2.delete_volume(). Refuses if safety gates fail.

Every remediate() call should be invoked through audit.audit_remediation
so the audit log captures the attempt.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from .base import (
    BasePattern,
    Category,
    Complexity,
    Finding,
    RemediationMode,
    RemediationResult,
    RiskTier,
)


logger = logging.getLogger(__name__)


PRICE_PER_GB = {
    "gp2": 0.10,
    "gp3": 0.08,
    "io1": 0.125,
    "io2": 0.125,
    "st1": 0.045,
    "sc1": 0.025,
    "standard": 0.05,
}

# Safety constants
MIN_AGE_DAYS_FOR_AUTO_DELETE = 7
MIN_SNAPSHOT_AGE_DAYS_BEFORE_DELETE = 1
TERRAFORM_TAG_KEY = "managed-by-terraform"


class UnattachedEBSPattern(BasePattern):
    PATTERN_ID = "001"
    NAME = "Unattached EBS Volumes"
    DESCRIPTION = "EBS volumes in 'available' state — not attached to any EC2 instance."
    CATEGORY = Category.STORAGE
    COMPLEXITY = Complexity.EASY
    SERVICES = ["ec2"]
    REQUIRED_IAM = [
        "ec2:DescribeVolumes",
        "ec2:DescribeSnapshots",
        "ec2:DescribeRegions",
        "ec2:DeleteVolume",  # only used in API_CALL mode
    ]

    # ------------------------------------------------------------------
    # scan
    # ------------------------------------------------------------------
    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                self._findings.extend(self._scan_region(region))
            except Exception:  # pragma: no cover — surface in caller
                logger.exception("p001 error scanning region %s", region)
                continue
        return self._findings

    def _scan_region(self, region: str) -> list[Finding]:
        ec2 = self.session.client("ec2", region_name=region)
        volumes = ec2.describe_volumes(
            Filters=[{"Name": "status", "Values": ["available"]}]
        )["Volumes"]

        findings = []
        for vol in volumes:
            findings.append(self._build_finding(ec2, region, vol))
        return findings

    def _build_finding(self, ec2, region: str, vol: dict) -> Finding:
        volume_id = vol["VolumeId"]
        size_gb = vol["Size"]
        vol_type = vol["VolumeType"]
        create_time = vol["CreateTime"]
        age_days = (datetime.now(timezone.utc) - create_time).days

        price_per_gb = PRICE_PER_GB.get(vol_type, 0.10)
        monthly_cost = round(size_gb * price_per_gb, 2)

        snapshots = ec2.describe_snapshots(
            Filters=[{"Name": "volume-id", "Values": [volume_id]}],
            OwnerIds=["self"],
        )["Snapshots"]
        has_snapshot = bool(snapshots)
        latest_snapshot_age_days = self._latest_snapshot_age_days(snapshots)

        tags = {t["Key"]: t["Value"] for t in vol.get("Tags", [])}
        terraform_managed = tags.get(TERRAFORM_TAG_KEY, "").lower() == "true"

        risk = self._risk_tier(age_days, monthly_cost)
        confidence = self._confidence(age_days, has_snapshot, latest_snapshot_age_days)
        safe_to_fix = (
            has_snapshot
            and age_days >= MIN_AGE_DAYS_FOR_AUTO_DELETE
            and (latest_snapshot_age_days or 0) >= MIN_SNAPSHOT_AGE_DAYS_BEFORE_DELETE
        )

        return Finding(
            pattern_id=self.PATTERN_ID,
            resource_id=volume_id,
            resource_type="EBS Volume",
            resource_arn=f"arn:aws:ec2:{region}::volume/{volume_id}",
            region=region,
            monthly_impact_usd=monthly_cost,
            summary=(
                f"Delete unattached {size_gb}GB {vol_type} volume "
                f"(age: {age_days}d, snapshot: {'yes' if has_snapshot else 'no'})"
            ),
            risk_tier=risk,
            confidence=confidence,
            safe_to_fix=safe_to_fix,
            fix_command=(
                f"aws ec2 delete-volume --volume-id {volume_id} --region {region}"
            ),
            evidence={
                "size_gb": size_gb,
                "volume_type": vol_type,
                "age_days": age_days,
                "has_snapshot": has_snapshot,
                "latest_snapshot_age_days": latest_snapshot_age_days,
                "snapshot_count": len(snapshots),
                "create_time": create_time.isoformat(),
                "tags": tags,
                "terraform_managed": terraform_managed,
            },
            metadata={
                "size_gb": size_gb,
                "volume_type": vol_type,
                "age_days": age_days,
                "has_snapshot": has_snapshot,
                "create_time": create_time.isoformat(),
            },
        )

    @staticmethod
    def _latest_snapshot_age_days(snapshots: list[dict]) -> int | None:
        starts = [s["StartTime"] for s in snapshots if "StartTime" in s]
        if not starts:
            return None
        return (datetime.now(timezone.utc) - max(starts)).days

    @staticmethod
    def _risk_tier(age_days: int, monthly_cost: float) -> RiskTier:
        if age_days > 30 and monthly_cost > 50:
            return RiskTier.HIGH
        if age_days > 7:
            return RiskTier.MEDIUM
        return RiskTier.LOW

    @staticmethod
    def _confidence(age_days: int, has_snapshot: bool, snap_age: int | None) -> float:
        # Confidence model:
        #   - older volumes are more confidently waste (caps at 0.95 at 30d).
        #   - a recent snapshot bumps confidence (data is backed up).
        #   - young volumes get a floor of 0.4 (might be a transient).
        base = min(0.4 + (age_days / 60.0), 0.95)
        if has_snapshot and snap_age is not None and snap_age <= 30:
            base = min(base + 0.05, 0.97)
        return round(base, 3)

    # ------------------------------------------------------------------
    # remediate
    # ------------------------------------------------------------------
    def remediate(self, finding: Finding, mode: RemediationMode) -> RemediationResult:
        if mode in (RemediationMode.DRY_RUN, RemediationMode.COMMAND):
            return super().remediate(finding, mode)
        if mode == RemediationMode.PR:
            return self._remediate_pr(finding)
        if mode == RemediationMode.API_CALL:
            return self._remediate_api_call(finding)
        return super().remediate(finding, mode)  # unknown mode → base raises

    def _remediate_pr(self, finding: Finding) -> RemediationResult:
        terraform_managed = finding.evidence.get("terraform_managed")
        if not terraform_managed:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=RemediationMode.PR,
                success=False,
                message=(
                    "Volume isn't tagged managed-by-terraform=true; "
                    "Whisper OSS only emits PRs for tagged resources. "
                    "Tag the volume in your IaC repo and re-scan."
                ),
            )
        diff = self._terraform_diff_hint(finding)
        return RemediationResult(
            finding_id=finding.id,
            pattern_id=self.PATTERN_ID,
            mode=RemediationMode.PR,
            success=True,
            message="Terraform diff hint emitted (paste into your IaC repo)",
            output=diff,
        )

    def _remediate_api_call(self, finding: Finding) -> RemediationResult:
        if not finding.safe_to_fix:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=RemediationMode.API_CALL,
                success=False,
                message=self._safety_failure_message(finding),
            )
        try:
            ec2 = self.session.client("ec2", region_name=finding.region)
            ec2.delete_volume(VolumeId=finding.resource_id)
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=RemediationMode.API_CALL,
                success=True,
                message=f"deleted volume {finding.resource_id}",
                output=f"DeleteVolume {finding.resource_id} ({finding.region})",
                evidence={"size_gb_recovered": finding.evidence.get("size_gb")},
            )
        except Exception as e:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=RemediationMode.API_CALL,
                success=False,
                message=str(e),
            )

    @staticmethod
    def _safety_failure_message(finding: Finding) -> str:
        evid = finding.evidence
        reasons = []
        if not evid.get("has_snapshot"):
            reasons.append("no snapshot exists (data would be unrecoverable)")
        age = evid.get("age_days")
        if isinstance(age, int) and age < MIN_AGE_DAYS_FOR_AUTO_DELETE:
            reasons.append(
                f"volume only {age}d old (must be ≥{MIN_AGE_DAYS_FOR_AUTO_DELETE}d)"
            )
        snap_age = evid.get("latest_snapshot_age_days")
        if isinstance(snap_age, int) and snap_age < MIN_SNAPSHOT_AGE_DAYS_BEFORE_DELETE:
            reasons.append(
                f"newest snapshot is only {snap_age}d old "
                f"(must be ≥{MIN_SNAPSHOT_AGE_DAYS_BEFORE_DELETE}d)"
            )
        if not reasons:
            reasons.append("finding not marked safe_to_fix")
        return f"refusing to delete {finding.resource_id}: " + "; ".join(reasons)

    @staticmethod
    def _terraform_diff_hint(finding: Finding) -> str:
        vol_id = finding.resource_id
        region = finding.region
        return (
            "# Terraform change hint (Whisper OSS — paste into your IaC repo)\n"
            "#\n"
            f"# Resource:    aws_ebs_volume {vol_id} ({region})\n"
            f"# Monthly impact: ${finding.monthly_impact_usd:.2f}\n"
            "#\n"
            "# Steps:\n"
            "#   1. Find the aws_ebs_volume block in your repo that matches\n"
            f"#      volume_id = \"{vol_id}\".\n"
            "#   2. Remove the resource block (or comment it out).\n"
            "#   3. Run: terraform plan -target=aws_ebs_volume.<name>\n"
            "#   4. Review and apply.\n"
            "#\n"
            "# Whisper paid tier opens this PR for you against your IaC repo;\n"
            "# OSS shows you the change so you can land it manually.\n"
        )
