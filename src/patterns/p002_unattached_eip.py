"""
Pattern 002: Unattached Elastic IPs
Detects EIPs not attached to any instance or ENI
"""


from .base import BasePattern, Complexity, Finding, Severity


class UnattachedEIPPattern(BasePattern):
    PATTERN_ID = "002"
    NAME = "Unattached Elastic IPs"
    DESCRIPTION = "Elastic IPs not attached to any instance (charged $0.005/hr)"
    COMPLEXITY = Complexity.EASY
    SERVICES = ["ec2"]

    HOURLY_COST = 0.005  # $0.005/hour when unattached
    MONTHLY_COST = HOURLY_COST * 24 * 30  # ~$3.60/month

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                ec2 = self.session.client('ec2', region_name=region)
                addresses = ec2.describe_addresses()['Addresses']

                for addr in addresses:
                    # Unattached if no InstanceId AND no NetworkInterfaceId
                    if 'InstanceId' not in addr and 'NetworkInterfaceId' not in addr:
                        allocation_id = addr.get('AllocationId', addr.get('PublicIp'))
                        public_ip = addr.get('PublicIp', 'N/A')

                        finding = Finding(
                            resource_id=allocation_id,
                            resource_type="Elastic IP",
                            region=region,
                            monthly_cost=self.MONTHLY_COST,
                            recommendation=f"Release unattached EIP {public_ip}",
                            severity=Severity.LOW,
                            safe_to_fix=True,  # Generally safe, but IP will be lost
                            fix_command=f"aws ec2 release-address --allocation-id {allocation_id} --region {region}",
                            metadata={
                                "public_ip": public_ip,
                                "domain": addr.get('Domain', 'vpc'),
                            }
                        )
                        self._findings.append(finding)

            except Exception as e:
                print(f"Error scanning {region}: {e}")
                continue

        return self._findings

    def fix(self, finding: Finding, dry_run: bool = True) -> bool:
        if dry_run:
            print(f"[DRY RUN] Would release EIP {finding.resource_id}")
            return True

        ec2 = self.session.client('ec2', region_name=finding.region)
        ec2.release_address(AllocationId=finding.resource_id)
        print(f"Released EIP {finding.resource_id}")
        return True
