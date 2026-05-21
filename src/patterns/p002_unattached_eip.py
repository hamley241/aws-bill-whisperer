"""
Pattern 002: Unattached Elastic IPs
Detects EIPs not attached to any instance or ENI
"""


from .base import BasePattern, Complexity, Finding, RemediationMode, RemediationResult, RiskTier, Category


class UnattachedEIPPattern(BasePattern):
    PATTERN_ID = "002"
    NAME = "Unattached Elastic IPs"
    DESCRIPTION = "Elastic IPs not attached to any instance (charged $0.005/hr)"
    COMPLEXITY = Complexity.EASY
    SERVICES = ["ec2"]
    CATEGORY = Category.NETWORK
    REQUIRED_IAM = ["ec2:DescribeAddresses", "ec2:DescribeRegions"]

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
                            pattern_id=self.PATTERN_ID,
                            resource_id=allocation_id,
                            resource_type="Elastic IP",
                            region=region,
                            monthly_impact_usd=self.MONTHLY_COST,
                            summary=f"Release unattached EIP {public_ip}",
                            risk_tier=RiskTier.LOW,
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

    def remediate(self, finding: Finding, mode: RemediationMode) -> RemediationResult:
        if mode != RemediationMode.API_CALL:
            return super().remediate(finding, mode)
        try:

            ec2 = self.session.client('ec2', region_name=finding.region)
            ec2.release_address(AllocationId=finding.resource_id)
            print(f"Released EIP {finding.resource_id}")
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=True,
                message="released EIP",
            )
        except Exception as e:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=False,
                message=str(e),
            )
