"""
Pattern 001: Unattached EBS Volumes
Detects EBS volumes in 'available' state (not attached to any instance)
"""

from datetime import datetime, timezone

from .base import BasePattern, Complexity, Finding, Severity


class UnattachedEBSPattern(BasePattern):
    PATTERN_ID = "001"
    NAME = "Unattached EBS Volumes"
    DESCRIPTION = "EBS volumes not attached to any EC2 instance"
    COMPLEXITY = Complexity.EASY
    SERVICES = ["ec2"]

    # Pricing (approximate, varies by region)
    PRICE_PER_GB = {
        "gp2": 0.10,
        "gp3": 0.08,
        "io1": 0.125,
        "io2": 0.125,
        "st1": 0.045,
        "sc1": 0.025,
        "standard": 0.05,
    }

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                ec2 = self.session.client('ec2', region_name=region)

                # Get unattached volumes
                volumes = ec2.describe_volumes(
                    Filters=[{'Name': 'status', 'Values': ['available']}]
                )['Volumes']

                for vol in volumes:
                    volume_id = vol['VolumeId']
                    size_gb = vol['Size']
                    vol_type = vol['VolumeType']
                    create_time = vol['CreateTime']

                    # Calculate age
                    age_days = (datetime.now(timezone.utc) - create_time).days

                    # Calculate monthly cost
                    price_per_gb = self.PRICE_PER_GB.get(vol_type, 0.10)
                    monthly_cost = size_gb * price_per_gb

                    # Check if snapshot exists (safety check)
                    snapshots = ec2.describe_snapshots(
                        Filters=[{'Name': 'volume-id', 'Values': [volume_id]}],
                        OwnerIds=['self']
                    )['Snapshots']
                    has_snapshot = len(snapshots) > 0

                    # Determine severity
                    if age_days > 30 and monthly_cost > 50:
                        severity = Severity.HIGH
                    elif age_days > 7:
                        severity = Severity.MEDIUM
                    else:
                        severity = Severity.LOW

                    finding = Finding(
                        resource_id=volume_id,
                        resource_type="EBS Volume",
                        region=region,
                        monthly_cost=monthly_cost,
                        recommendation=f"Delete unattached volume (age: {age_days}d, {size_gb}GB {vol_type})",
                        severity=severity,
                        safe_to_fix=has_snapshot and age_days > 7,
                        fix_command=f"aws ec2 delete-volume --volume-id {volume_id} --region {region}",
                        metadata={
                            "size_gb": size_gb,
                            "volume_type": vol_type,
                            "age_days": age_days,
                            "has_snapshot": has_snapshot,
                            "create_time": create_time.isoformat(),
                        }
                    )
                    self._findings.append(finding)

            except Exception as e:
                print(f"Error scanning {region}: {e}")
                continue

        return self._findings

    def fix(self, finding: Finding, dry_run: bool = True) -> bool:
        if not finding.safe_to_fix:
            raise ValueError(f"Volume {finding.resource_id} has no snapshot - unsafe to delete")

        if dry_run:
            print(f"[DRY RUN] Would delete volume {finding.resource_id}")
            return True

        ec2 = self.session.client('ec2', region_name=finding.region)
        ec2.delete_volume(VolumeId=finding.resource_id)
        print(f"Deleted volume {finding.resource_id}")
        return True
