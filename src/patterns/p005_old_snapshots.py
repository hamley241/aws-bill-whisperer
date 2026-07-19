"""
Pattern 005: Old EBS Snapshots
Detects EBS snapshots older than a threshold (default 90 days) that may no longer be needed
"""

import logging
from datetime import datetime, timedelta, timezone

from .base import BasePattern, Complexity, Finding, RemediationMode, RemediationResult, RiskTier, Category


logger = logging.getLogger(__name__)


class OldSnapshotsPattern(BasePattern):
    PATTERN_ID = "005"
    NAME = "Old EBS Snapshots"
    DESCRIPTION = "EBS snapshots older than threshold that may no longer be needed"
    COMPLEXITY = Complexity.EASY
    SERVICES = ["ec2"]
    CATEGORY = Category.STORAGE
    REQUIRED_IAM = ["ec2:DescribeSnapshots", "ec2:DescribeImages", "ec2:DescribeRegions"]

    # Snapshot storage cost per GB per month (approximate)
    SNAPSHOT_COST_PER_GB = 0.05

    def __init__(self, session=None, threshold_days: int = 90):
        super().__init__(session)
        self.threshold_days = threshold_days
        self.threshold_date = datetime.now(timezone.utc) - timedelta(days=threshold_days)

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                ec2 = self.session.client('ec2', region_name=region)

                # Get all snapshots owned by this account using pagination
                # (describe_snapshots returns max 1000 per call, so use paginator)
                paginator = ec2.get_paginator('describe_snapshots')
                snapshots = []
                for page in paginator.paginate(OwnerIds=['self']):
                    snapshots.extend(page['Snapshots'])

                # Get all AMIs to check if snapshot is in use
                amis = ec2.describe_images(Owners=['self'])['Images']
                ami_snapshot_ids = set()
                for ami in amis:
                    for bdm in ami.get('BlockDeviceMappings', []):
                        if 'Ebs' in bdm and 'SnapshotId' in bdm['Ebs']:
                            ami_snapshot_ids.add(bdm['Ebs']['SnapshotId'])

                for snapshot in snapshots:
                    snapshot_id = snapshot['SnapshotId']
                    start_time = snapshot['StartTime']
                    volume_size = snapshot['VolumeSize']
                    description = snapshot.get('Description', '')

                    # Skip if newer than threshold
                    if start_time > self.threshold_date:
                        continue

                    # Calculate age
                    age_days = (datetime.now(timezone.utc) - start_time).days

                    # Calculate monthly cost
                    monthly_impact_usd= volume_size * self.SNAPSHOT_COST_PER_GB

                    # Check if attached to an AMI
                    attached_to_ami = snapshot_id in ami_snapshot_ids

                    # Determine risk_tier
                    if age_days > 365 and not attached_to_ami:
                        risk_tier= RiskTier.HIGH
                    elif age_days > 180 and not attached_to_ami:
                        risk_tier= RiskTier.MEDIUM
                    else:
                        risk_tier= RiskTier.LOW

                    # Safe to delete if not attached to AMI and older than threshold
                    safe_to_fix = not attached_to_ami and age_days > self.threshold_days

                    # Build summary
                    if attached_to_ami:
                        summary= f"Review old snapshot used by AMI (age: {age_days}d, {volume_size}GB)"
                    else:
                        summary= f"Delete unused old snapshot (age: {age_days}d, {volume_size}GB)"

                    finding = Finding(
                        pattern_id=self.PATTERN_ID,
                        resource_id=snapshot_id,
                        resource_type="EBS Snapshot",
                        region=region,
                        monthly_impact_usd=monthly_impact_usd,
                        summary=summary,
                        risk_tier=risk_tier,
                        safe_to_fix=safe_to_fix,
                        fix_command=f"aws ec2 delete-snapshot --snapshot-id {snapshot_id} --region {region}" if safe_to_fix else None,
                        metadata={
                            "volume_size_gb": volume_size,
                            "age_days": age_days,
                            "attached_to_ami": attached_to_ami,
                            "start_time": start_time.isoformat(),
                            "description": description,
                            "threshold_days": self.threshold_days,
                        }
                    )
                    self._findings.append(finding)

            except Exception as exc:
                logger.exception(
                    "p005 error scanning snapshots in region %s", region,
                    extra={
                        "pattern_id": self.PATTERN_ID,
                        "region": region,
                        "outcome": "failed",
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                    },
                )
                continue

        return self._findings

    def remediate(self, finding: Finding, mode: RemediationMode) -> RemediationResult:
        if mode != RemediationMode.API_CALL:
            return super().remediate(finding, mode)
        if not finding.safe_to_fix:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=False,
                message=f"Snapshot {finding.resource_id} is attached to AMI - unsafe to delete",
            )
        try:
        


            ec2 = self.session.client('ec2', region_name=finding.region)
            ec2.delete_snapshot(SnapshotId=finding.resource_id)
            logger.info(
                "p005 deleted snapshot %s", finding.resource_id,
                extra={
                    "pattern_id": self.PATTERN_ID,
                    "region": finding.region,
                    "outcome": "ok",
                    "snapshot_id": finding.resource_id,
                },
            )
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=True,
                message="deleted snapshot",
            )
        except Exception as e:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=False,
                message=str(e),
            )
