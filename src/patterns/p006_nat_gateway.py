"""
Pattern 006: NAT Gateway Optimization
Detects NAT Gateways with high data transfer costs and suggests alternatives
"""

from datetime import datetime, timedelta, timezone

from .base import BasePattern, Complexity, Finding, Severity


class NatGatewayOptimizationPattern(BasePattern):
    PATTERN_ID = "006"
    NAME = "NAT Gateway Optimization"
    DESCRIPTION = "NAT Gateways with expensive data transfer that could be optimized"
    COMPLEXITY = Complexity.MEDIUM
    SERVICES = ["ec2", "cloudwatch"]

    # NAT Gateway pricing (approximate, varies by region)
    NAT_HOURLY_COST = 0.045  # $0.045/hour
    DATA_TRANSFER_COST = 0.045  # $0.045/GB

    def __init__(self, session=None, monthly_transfer_threshold_gb: int = 100):
        super().__init__(session)
        self.monthly_transfer_threshold_gb = monthly_transfer_threshold_gb
        # Look at last 30 days for data transfer metrics
        self.end_time = datetime.now(timezone.utc)
        self.start_time = self.end_time - timedelta(days=30)

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                ec2 = self.session.client('ec2', region_name=region)
                cloudwatch = self.session.client('cloudwatch', region_name=region)

                # Get all NAT Gateways
                nat_gateways = ec2.describe_nat_gateways(
                    Filters=[{'Name': 'state', 'Values': ['available']}]
                )['NatGateways']

                for nat_gw in nat_gateways:
                    nat_gw_id = nat_gw['NatGatewayId']
                    subnet_id = nat_gw['SubnetId']
                    vpc_id = nat_gw['VpcId']
                    create_time = nat_gw['CreateTime']

                    # Calculate monthly NAT Gateway base cost (hourly * 24 * 30)
                    monthly_base_cost = self.NAT_HOURLY_COST * 24 * 30

                    # Get data transfer metrics from CloudWatch
                    total_bytes_out = self._get_nat_gateway_data_transfer(
                        cloudwatch, nat_gw_id
                    )

                    # Convert bytes to GB
                    total_gb_out = total_bytes_out / (1024**3) if total_bytes_out else 0

                    # Calculate data transfer cost for the month
                    monthly_transfer_cost = total_gb_out * self.DATA_TRANSFER_COST
                    total_monthly_cost = monthly_base_cost + monthly_transfer_cost

                    # Only flag if transfer exceeds threshold
                    if total_gb_out < self.monthly_transfer_threshold_gb:
                        continue

                    # Determine severity based on transfer volume
                    if total_gb_out > 1000:  # > 1TB
                        severity = Severity.HIGH
                    elif total_gb_out > 500:  # > 500GB
                        severity = Severity.MEDIUM
                    else:
                        severity = Severity.LOW

                    # Calculate potential savings with alternatives
                    # VPC endpoints could eliminate some S3/DynamoDB transfer
                    # NAT instances are cheaper for dev/test workloads
                    estimated_vpc_endpoint_savings = min(total_gb_out * 0.3, total_gb_out) * self.DATA_TRANSFER_COST

                    # Build recommendation
                    recommendations = []
                    if total_gb_out > 200:
                        recommendations.append(f"Consider VPC endpoints for S3/DynamoDB (potential savings: ${estimated_vpc_endpoint_savings:.2f}/month)")
                    if 'dev' in subnet_id.lower() or 'test' in vpc_id.lower():
                        recommendations.append("Consider NAT instance for dev/test workloads (60-70% cost savings)")
                    recommendations.append(f"Review data transfer patterns ({total_gb_out:.1f}GB/month)")

                    recommendation = "; ".join(recommendations)

                    # Age in days
                    age_days = (datetime.now(timezone.utc) - create_time).days if create_time else 0

                    finding = Finding(
                        resource_id=nat_gw_id,
                        resource_type="NAT Gateway",
                        region=region,
                        monthly_cost=total_monthly_cost,
                        recommendation=recommendation,
                        severity=severity,
                        safe_to_fix=False,  # NAT Gateway changes need careful planning
                        fix_command=None,
                        metadata={
                            "subnet_id": subnet_id,
                            "vpc_id": vpc_id,
                            "monthly_base_cost": monthly_base_cost,
                            "monthly_transfer_cost": monthly_transfer_cost,
                            "total_gb_transferred": round(total_gb_out, 2),
                            "transfer_threshold_gb": self.monthly_transfer_threshold_gb,
                            "age_days": age_days,
                            "estimated_vpc_endpoint_savings": round(estimated_vpc_endpoint_savings, 2),
                        }
                    )
                    self._findings.append(finding)

            except Exception as e:
                print(f"Error scanning NAT Gateways in {region}: {e}")
                continue

        return self._findings

    def _get_nat_gateway_data_transfer(self, cloudwatch, nat_gw_id: str) -> float:
        """Get total bytes transferred out from NAT Gateway in the last 30 days"""
        try:
            # Get BytesOutToDestination metric
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/NATGateway',
                MetricName='BytesOutToDestination',
                Dimensions=[
                    {'Name': 'NatGatewayId', 'Value': nat_gw_id}
                ],
                StartTime=self.start_time,
                EndTime=self.end_time,
                Period=86400,  # Daily aggregation
                Statistics=['Sum']
            )

            total_bytes = sum(point['Sum'] for point in response['Datapoints'])

            # Also get BytesOutToSource (responses back to private subnet)
            response2 = cloudwatch.get_metric_statistics(
                Namespace='AWS/NATGateway',
                MetricName='BytesOutToSource',
                Dimensions=[
                    {'Name': 'NatGatewayId', 'Value': nat_gw_id}
                ],
                StartTime=self.start_time,
                EndTime=self.end_time,
                Period=86400,
                Statistics=['Sum']
            )

            total_bytes += sum(point['Sum'] for point in response2['Datapoints'])
            return total_bytes

        except Exception as e:
            print(f"Error getting CloudWatch metrics for NAT Gateway {nat_gw_id}: {e}")
            return 0.0

    def fix(self, finding: Finding, dry_run: bool = True) -> bool:
        # NAT Gateway optimization requires manual intervention
        # Cannot be automatically fixed due to network dependencies
        raise NotImplementedError(
            "NAT Gateway optimization requires manual review and planning. "
            "Consider VPC endpoints, NAT instances, or traffic pattern analysis."
        )
