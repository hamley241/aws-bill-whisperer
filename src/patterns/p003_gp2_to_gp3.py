"""
Pattern 003: GP2 to GP3 Migration
Detects EBS volumes using gp2 that could be migrated to gp3 for ~20% savings
"""

from .base import BasePattern, Complexity, Finding, Severity


class GP2ToGP3Pattern(BasePattern):
    PATTERN_ID = "003"
    NAME = "GP2 to GP3 Migration"
    DESCRIPTION = "EBS gp2 volumes that could save ~20% by migrating to gp3"
    COMPLEXITY = Complexity.EASY
    SERVICES = ["ec2", "ebs"]

    # Pricing (per GB-month, approximate for us-east-1)
    GP2_PRICE = 0.10
    GP3_PRICE = 0.08
    SAVINGS_RATE = 0.20  # 20% savings

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                ec2 = self.session.client("ec2", region_name=region)

                # Get all gp2 volumes
                paginator = ec2.get_paginator("describe_volumes")
                for page in paginator.paginate(
                    Filters=[{"Name": "volume-type", "Values": ["gp2"]}]
                ):
                    for vol in page["Volumes"]:
                        volume_id = vol["VolumeId"]
                        size_gb = vol["Size"]
                        state = vol["State"]
                        iops = vol.get("Iops", 100)

                        # Calculate current monthly cost
                        monthly_cost = size_gb * self.GP2_PRICE
                        potential_savings = monthly_cost * self.SAVINGS_RATE * 0.5  # 20% of current cost

                        # Determine if gp3 is suitable
                        can_migrate = self._can_migrate_to_gp3(vol)

                        if not can_migrate:
                            continue

                        # Determine severity based on volume size
                        if size_gb > 500:
                            severity = Severity.CRITICAL
                        elif size_gb > 100:
                            severity = Severity.HIGH
                        elif size_gb > 50:
                            severity = Severity.MEDIUM
                        else:
                            severity = Severity.LOW

                        # Build recommendation
                        recommendation = (
                            f"Migrate gp2 volume ({size_gb}GB, {iops} IOPS) to gp3. "
                            f"Potential savings: ~${potential_savings:.2f}/mo ({20}% reduction). "
                            f"Command: aws ec2 modify-volume --volume-id {volume_id} --volume-type gp3"
                        )

                        # gp3 baseline IOPS is 3000, gp2 is dynamic (max 16000)
                        # gp3 baselne throughput is 125 MB/s (for <170GiB) or 250 (for >170GiB)
                        gp3_iops = min(iops, 16000)  # gp3 supports up to 16,000 IOPS
                        actual_iops = max(gp3_iops, 3000)  # gp3 minimum is 3000

                        finding = Finding(
                            resource_id=volume_id,
                            resource_type="EBS Volume",
                            region=region,
                            monthly_cost=potential_savings,
                            recommendation=recommendation,
                            severity=severity,
                            safe_to_fix=True,  # gp2->gp3 migration is safe and non-disruptive
                            fix_command=f"aws ec2 modify-volume --volume-id {volume_id} --volume-type gp3 --iops {actual_iops} --region {region}",
                            metadata={
                                "size_gb": size_gb,
                                "current_type": "gp2",
                                "proposed_type": "gp3",
                                "current_monthly_cost": monthly_cost,
                                "potential_savings": potential_savings,
                                "state": state,
                                "current_iops": iops,
                                "proposed_iops": actual_iops,
                                "attachment_count": len(vol.get("Attachments", [])),
                            }
                        )
                        self._findings.append(finding)

            except Exception as e:
                print(f"Error scanning {region}: {e}")
                continue

        return self._findings

    def _can_migrate_to_gp3(self, vol: dict) -> bool:
        """Check if volume can be migrated to gp3."""
        # GP3 limitations:
        # - Max 64,000 IOPS and 1,000 MiB/s throughput
        # - Size range: 1 GiB - 16 TiB (same as gp2)
        size_gb = vol.get("Size", 0)
        iops = vol.get("Iops", 100)

        # Size must be <= 16 TB (16384 GB)
        if size_gb > 16384:
            return False

        # GP3 supports up to 64,000 IOPS, but baseline is 3000
        # If gp2 is bursting above that, migration might be tricky
        # For now, assume anything with less than 16000 IOPS is safe
        if iops > 16000:
            return False

        return True

    def fix(self, finding: Finding, dry_run: bool = True) -> bool:
        """Migrate volume from gp2 to gp3."""
        if not finding.safe_to_fix:
            raise ValueError(f"Finding {finding.resource_id} is not marked safe to fix")

        if dry_run:
            print(f"[DRY RUN] Would migrate volume {finding.resource_id} from gp2 to gp3")
            print(f"[DRY RUN] Command: {finding.fix_command}")
            return True

        ec2 = self.session.client("ec2", region_name=finding.region)
        result = ec2.modify_volume(
            VolumeId=finding.resource_id,
            VolumeType="gp3",
            Iops=finding.metadata.get("proposed_iops", 3000)
        )
        print(f"Modified volume {finding.resource_id}: {result['VolumeModification']['Status']}")
        return True
