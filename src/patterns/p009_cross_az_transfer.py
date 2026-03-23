"""
Pattern 009: Cross-AZ Data Transfer
Detects resources generating high cross-AZ data transfer costs.
Cross-AZ data transfer is the "silent killer" of AWS bills at $0.01/GB each way.
"""

from datetime import datetime, timedelta, timezone

from .base import BasePattern, Complexity, Finding, Severity


class CrossAZTransferPattern(BasePattern):
    PATTERN_ID = "009"
    NAME = "Cross-AZ Data Transfer"
    DESCRIPTION = "Resources with high cross-AZ data transfer costs"
    COMPLEXITY = Complexity.HARD
    SERVICES = ["ec2", "rds", "elasticache", "cloudwatch"]

    # Cross-AZ pricing
    CROSS_AZ_COST_PER_GB = 0.01  # $0.01/GB each direction (so $0.02 round trip)

    def __init__(self, session=None, monthly_threshold_gb: float = 100.0):
        super().__init__(session)
        self.monthly_threshold_gb = monthly_threshold_gb
        self.end_time = datetime.now(timezone.utc)
        self.start_time = self.end_time - timedelta(days=30)

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                # Check RDS Multi-AZ instances (always generate cross-AZ traffic)
                self._scan_rds_multi_az(region)

                # Check ElastiCache clusters spanning AZs
                self._scan_elasticache_cross_az(region)

                # Check EC2 instances with high NetworkOut in multi-AZ setups
                self._scan_ec2_cross_az(region)

            except Exception as e:
                print(f"Error scanning cross-AZ in {region}: {e}")
                continue

        return self._findings

    def _scan_rds_multi_az(self, region: str) -> None:
        """Detect RDS Multi-AZ instances with potential cross-AZ replication costs"""
        try:
            rds = self.session.client('rds', region_name=region)
            cloudwatch = self.session.client('cloudwatch', region_name=region)

            instances = rds.describe_db_instances()['DBInstances']

            for instance in instances:
                if not instance.get('MultiAZ', False):
                    continue

                db_id = instance['DBInstanceIdentifier']
                db_class = instance['DBInstanceClass']
                engine = instance['Engine']
                storage_gb = instance.get('AllocatedStorage', 0)

                # Get write throughput to estimate replication traffic
                write_bytes = self._get_rds_write_bytes(cloudwatch, db_id)
                write_gb = write_bytes / (1024**3) if write_bytes else 0

                if write_gb < self.monthly_threshold_gb:
                    continue

                # Cross-AZ replication doubles write traffic (primary → standby)
                monthly_cross_az_cost = write_gb * self.CROSS_AZ_COST_PER_GB * 2

                # Determine severity
                if monthly_cross_az_cost > 100:
                    severity = Severity.HIGH
                elif monthly_cross_az_cost > 25:
                    severity = Severity.MEDIUM
                else:
                    severity = Severity.LOW

                recommendation = (
                    f"RDS Multi-AZ with {write_gb:.1f}GB/month write traffic. "
                    f"Cross-AZ replication cost: ${monthly_cross_az_cost:.2f}/month. "
                    "Consider: (1) Read replicas in same AZ as app tier, "
                    "(2) Batch writes to reduce replication frequency, "
                    "(3) Single-AZ for dev/test environments."
                )

                finding = Finding(
                    resource_id=db_id,
                    resource_type="RDS Multi-AZ Instance",
                    region=region,
                    monthly_cost=monthly_cross_az_cost,
                    recommendation=recommendation,
                    severity=severity,
                    safe_to_fix=False,
                    fix_command=None,
                    metadata={
                        "db_class": db_class,
                        "engine": engine,
                        "storage_gb": storage_gb,
                        "monthly_write_gb": round(write_gb, 2),
                        "multi_az": True,
                        "cross_az_cost_per_gb": self.CROSS_AZ_COST_PER_GB,
                    }
                )
                self._findings.append(finding)

        except Exception as e:
            print(f"Error scanning RDS Multi-AZ in {region}: {e}")

    def _scan_elasticache_cross_az(self, region: str) -> None:
        """Detect ElastiCache clusters with cross-AZ replication"""
        try:
            elasticache = self.session.client('elasticache', region_name=region)
            cloudwatch = self.session.client('cloudwatch', region_name=region)

            # Check Redis replication groups
            replication_groups = elasticache.describe_replication_groups().get(
                'ReplicationGroups', []
            )

            for rg in replication_groups:
                rg_id = rg['ReplicationGroupId']
                node_groups = rg.get('NodeGroups', [])

                # Check if nodes span multiple AZs
                azs_used = set()
                for ng in node_groups:
                    for member in ng.get('NodeGroupMembers', []):
                        az = member.get('PreferredAvailabilityZone', '')
                        if az:
                            azs_used.add(az)

                if len(azs_used) < 2:
                    continue  # Single AZ, no cross-AZ traffic

                # Get network traffic metrics
                network_bytes = self._get_elasticache_network_bytes(
                    cloudwatch, rg_id, node_groups
                )
                network_gb = network_bytes / (1024**3) if network_bytes else 0

                if network_gb < self.monthly_threshold_gb:
                    continue

                # Estimate cross-AZ portion (replication between nodes in different AZs)
                cross_az_gb = network_gb * 0.5  # Conservative estimate
                monthly_cross_az_cost = cross_az_gb * self.CROSS_AZ_COST_PER_GB * 2

                if monthly_cross_az_cost > 50:
                    severity = Severity.HIGH
                elif monthly_cross_az_cost > 15:
                    severity = Severity.MEDIUM
                else:
                    severity = Severity.LOW

                recommendation = (
                    f"ElastiCache cluster spans {len(azs_used)} AZs with "
                    f"~{cross_az_gb:.1f}GB/month cross-AZ traffic. "
                    f"Estimated cost: ${monthly_cross_az_cost:.2f}/month. "
                    "Consider: (1) Colocate replicas with app tier, "
                    "(2) Use cluster mode for sharding, "
                    "(3) Evaluate if multi-AZ is required."
                )

                finding = Finding(
                    resource_id=rg_id,
                    resource_type="ElastiCache Replication Group",
                    region=region,
                    monthly_cost=monthly_cross_az_cost,
                    recommendation=recommendation,
                    severity=severity,
                    safe_to_fix=False,
                    fix_command=None,
                    metadata={
                        "azs_used": list(azs_used),
                        "num_node_groups": len(node_groups),
                        "monthly_network_gb": round(network_gb, 2),
                        "estimated_cross_az_gb": round(cross_az_gb, 2),
                    }
                )
                self._findings.append(finding)

        except Exception as e:
            print(f"Error scanning ElastiCache in {region}: {e}")

    def _scan_ec2_cross_az(self, region: str) -> None:
        """Detect EC2 instances with high network out that may be cross-AZ"""
        try:
            ec2 = self.session.client('ec2', region_name=region)
            cloudwatch = self.session.client('cloudwatch', region_name=region)

            # Get instances with associated load balancers or in ASGs
            # These are more likely to have cross-AZ traffic

            # Get all running instances
            instances = ec2.describe_instances(
                Filters=[{'Name': 'instance-state-name', 'Values': ['running']}]
            )['Reservations']

            # Group instances by VPC and AZ to find multi-AZ deployments
            vpc_az_map = {}  # vpc_id -> {az -> [instance_ids]}
            instance_details = {}

            for reservation in instances:
                for instance in reservation['Instances']:
                    instance_id = instance['InstanceId']
                    vpc_id = instance.get('VpcId', 'no-vpc')
                    az = instance['Placement']['AvailabilityZone']
                    instance_type = instance['InstanceType']

                    if vpc_id not in vpc_az_map:
                        vpc_az_map[vpc_id] = {}
                    if az not in vpc_az_map[vpc_id]:
                        vpc_az_map[vpc_id][az] = []

                    vpc_az_map[vpc_id][az].append(instance_id)
                    instance_details[instance_id] = {
                        'vpc_id': vpc_id,
                        'az': az,
                        'instance_type': instance_type,
                    }

            # Check VPCs with instances in multiple AZs
            for vpc_id, az_instances in vpc_az_map.items():
                if len(az_instances) < 2:
                    continue  # Single AZ VPC, no cross-AZ traffic

                # Check high-traffic instances in multi-AZ VPCs
                for az, instance_ids in az_instances.items():
                    for instance_id in instance_ids:
                        network_bytes = self._get_ec2_network_bytes(
                            cloudwatch, instance_id
                        )
                        network_gb = network_bytes / (1024**3) if network_bytes else 0

                        if network_gb < self.monthly_threshold_gb * 2:
                            continue  # Need higher threshold for EC2

                        # Estimate cross-AZ portion based on multi-AZ deployment
                        # If 3 AZs, ~66% of traffic might be cross-AZ
                        num_azs = len(az_instances)
                        cross_az_ratio = (num_azs - 1) / num_azs
                        cross_az_gb = network_gb * cross_az_ratio * 0.5
                        monthly_cross_az_cost = cross_az_gb * self.CROSS_AZ_COST_PER_GB * 2

                        if monthly_cross_az_cost < 10:
                            continue  # Skip small costs for EC2

                        if monthly_cross_az_cost > 100:
                            severity = Severity.HIGH
                        elif monthly_cross_az_cost > 30:
                            severity = Severity.MEDIUM
                        else:
                            severity = Severity.LOW

                        details = instance_details[instance_id]
                        recommendation = (
                            f"High-traffic EC2 in {num_azs}-AZ deployment. "
                            f"~{cross_az_gb:.1f}GB/month cross-AZ traffic estimate. "
                            f"Potential cost: ${monthly_cross_az_cost:.2f}/month. "
                            "Consider: (1) AZ-aware routing, "
                            "(2) Colocate dependent services, "
                            "(3) Use VPC endpoints for AWS services."
                        )

                        finding = Finding(
                            resource_id=instance_id,
                            resource_type="EC2 Instance (Cross-AZ)",
                            region=region,
                            monthly_cost=monthly_cross_az_cost,
                            recommendation=recommendation,
                            severity=severity,
                            safe_to_fix=False,
                            fix_command=None,
                            metadata={
                                "vpc_id": vpc_id,
                                "az": az,
                                "instance_type": details['instance_type'],
                                "num_azs_in_vpc": num_azs,
                                "monthly_network_gb": round(network_gb, 2),
                                "estimated_cross_az_gb": round(cross_az_gb, 2),
                            }
                        )
                        self._findings.append(finding)

        except Exception as e:
            print(f"Error scanning EC2 cross-AZ in {region}: {e}")

    def _get_rds_write_bytes(self, cloudwatch, db_id: str) -> float:
        """Get RDS write throughput over the past 30 days"""
        try:
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/RDS',
                MetricName='WriteThroughput',
                Dimensions=[{'Name': 'DBInstanceIdentifier', 'Value': db_id}],
                StartTime=self.start_time,
                EndTime=self.end_time,
                Period=86400,
                Statistics=['Sum']
            )
            # WriteThroughput is bytes/sec, multiply by period to get total
            total = sum(dp['Sum'] * 86400 for dp in response['Datapoints'])
            return total
        except Exception:
            return 0.0

    def _get_elasticache_network_bytes(
        self, cloudwatch, rg_id: str, node_groups: list
    ) -> float:
        """Get ElastiCache network traffic"""
        try:
            total_bytes = 0
            for ng in node_groups:
                for member in ng.get('NodeGroupMembers', []):
                    cache_cluster_id = member.get('CacheClusterId', '')
                    if not cache_cluster_id:
                        continue

                    response = cloudwatch.get_metric_statistics(
                        Namespace='AWS/ElastiCache',
                        MetricName='NetworkBytesOut',
                        Dimensions=[
                            {'Name': 'CacheClusterId', 'Value': cache_cluster_id}
                        ],
                        StartTime=self.start_time,
                        EndTime=self.end_time,
                        Period=86400,
                        Statistics=['Sum']
                    )
                    total_bytes += sum(dp['Sum'] for dp in response['Datapoints'])

            return total_bytes
        except Exception:
            return 0.0

    def _get_ec2_network_bytes(self, cloudwatch, instance_id: str) -> float:
        """Get EC2 NetworkOut over the past 30 days"""
        try:
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/EC2',
                MetricName='NetworkOut',
                Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
                StartTime=self.start_time,
                EndTime=self.end_time,
                Period=86400,
                Statistics=['Sum']
            )
            return sum(dp['Sum'] for dp in response['Datapoints'])
        except Exception:
            return 0.0

    def fix(self, finding: Finding, dry_run: bool = True) -> bool:
        """
        Cross-AZ optimization requires architectural changes.
        Cannot be auto-fixed.
        """
        raise NotImplementedError(
            "Cross-AZ data transfer optimization requires architectural review. "
            "Consider AZ-aware routing, colocation strategies, or VPC endpoints "
            "based on the specific resource type and traffic patterns."
        )
