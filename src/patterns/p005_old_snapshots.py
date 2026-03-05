"""
Pattern 005: Old EBS Snapshots
Detects EBS snapshots older than a threshold (default 90 days) that may no longer be needed
"""

from datetime import datetime, timedelta, timezone

from .base import BasePattern, Complexity, Finding, Severity


class OldSnapshotsPattern(BasePattern):
    PATTERN_ID = "005"
    NAME = "Old EBS Snapshots"
    DESCRIPTION = "EBS snapshots older than threshold that may no longer be needed"
    COMPLEXITY = Complexity.EASY
    SERVICES = ["ec2"]

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

                # Get all snapshots owned by this account
                snapshots = ec2.describe_snapshots(
                    OwnerIds=['self'],
                    MaxResults=1000  # Pagination might be needed for large accounts
                )['Snapshots']

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
                    monthly_cost = volume_size * self.SNAPSHOT_COST_PER_GB

                    # Check if attached to an AMI
                    attached_to_ami = snapshot_id in ami_snapshot_ids

                    # Determine severity
                    if age_days > 365 and not attached_to_ami:
                        severity = Severity.HIGH
                    elif age_days > 180 and not attached_to_ami:
                        severity = Severity.MEDIUM
                    else:
                        severity = Severity.LOW

                    # Safe to delete if not attached to AMI and older than threshold
                    safe_to_fix = not attached_to_ami and age_days > self.threshold_days

                    # Build recommendation
                    if attached_to_ami:
                        recommendation = f"Review old snapshot used by AMI (age: {age_days}d, {volume_size}GB)"
                    else:
                        recommendation = f"Delete unused old snapshot (age: {age_days}d, {volume_size}GB)"

                    finding = Finding(
                        resource_id=snapshot_id,
                        resource_type="EBS Snapshot",
                        region=region,
                        monthly_cost=monthly_cost,
                        recommendation=recommendation,
                        severity=severity,
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

            except Exception as e:
                print(f"Error scanning snapshots in {region}: {e}")
                continue

        return self._findings

    def fix(self, finding: Finding, dry_run: bool = True) -> bool:
        if not finding.safe_to_fix:
            raise ValueError(f"Snapshot {finding.resource_id} is attached to AMI - unsafe to delete")

        if dry_run:
            print(f"[DRY RUN] Would delete snapshot {finding.resource_id}")
            return True

        ec2 = self.session.client('ec2', region_name=finding.region)
        ec2.delete_snapshot(SnapshotId=finding.resource_id)
        print(f"Deleted snapshot {finding.resource_id}")
        return True
