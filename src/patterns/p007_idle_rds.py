"""
Pattern 007: Idle RDS Instances
Detects RDS instances with low connections and CPU utilization that may be oversized or unused
"""

from datetime import datetime, timedelta, timezone

from .base import BasePattern, Complexity, Finding, Severity


class IdleRDSPattern(BasePattern):
    PATTERN_ID = "007"
    NAME = "Idle RDS Instances"
    DESCRIPTION = "RDS instances with low connections and CPU utilization"
    COMPLEXITY = Complexity.MEDIUM
    SERVICES = ["rds", "cloudwatch"]

    # Approximate RDS pricing per hour (varies by region and instance type)
    # These are rough estimates for common instance types
    INSTANCE_HOURLY_COSTS = {
        'db.t3.micro': 0.017,
        'db.t3.small': 0.034,
        'db.t3.medium': 0.068,
        'db.t3.large': 0.136,
        'db.t3.xlarge': 0.272,
        'db.t3.2xlarge': 0.544,
        'db.t4g.micro': 0.016,
        'db.t4g.small': 0.032,
        'db.t4g.medium': 0.064,
        'db.t4g.large': 0.128,
        'db.m5.large': 0.192,
        'db.m5.xlarge': 0.384,
        'db.m5.2xlarge': 0.768,
        'db.m5.4xlarge': 1.536,
        'db.r5.large': 0.240,
        'db.r5.xlarge': 0.480,
        'db.r5.2xlarge': 0.960,
    }

    def __init__(self, session=None, cpu_threshold: float = 5.0, connection_threshold: int = 1):
        super().__init__(session)
        self.cpu_threshold = cpu_threshold  # Average CPU % over period
        self.connection_threshold = connection_threshold  # Average connections
        # Look at last 14 days for metrics
        self.end_time = datetime.now(timezone.utc)
        self.start_time = self.end_time - timedelta(days=14)

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                rds = self.session.client('rds', region_name=region)
                cloudwatch = self.session.client('cloudwatch', region_name=region)

                # Get all RDS instances
                instances = rds.describe_db_instances()['DBInstances']

                for instance in instances:
                    # Skip if not available
                    if instance['DBInstanceStatus'] != 'available':
                        continue

                    db_instance_id = instance['DBInstanceIdentifier']
                    db_instance_class = instance['DBInstanceClass']
                    engine = instance['Engine']
                    multi_az = instance['MultiAZ']
                    create_time = instance['InstanceCreateTime']

                    # Get CloudWatch metrics
                    avg_cpu = self._get_average_cpu_utilization(cloudwatch, db_instance_id)
                    avg_connections = self._get_average_connections(cloudwatch, db_instance_id)

                    # Skip if we couldn't get metrics or instance is busy
                    if avg_cpu is None or avg_connections is None:
                        continue

                    # Only flag if both CPU and connections are low
                    if avg_cpu >= self.cpu_threshold or avg_connections >= self.connection_threshold:
                        continue

                    # Calculate monthly cost
                    hourly_cost = self.INSTANCE_HOURLY_COSTS.get(
                        db_instance_class,
                        0.10  # Default estimate if unknown
                    )

                    # MultiAZ doubles the cost
                    if multi_az:
                        hourly_cost *= 2

                    monthly_cost = hourly_cost * 24 * 30

                    # Determine severity
                    if monthly_cost > 200 and avg_cpu < 1.0:
                        severity = Severity.HIGH
                    elif monthly_cost > 50 and avg_cpu < 2.0:
                        severity = Severity.MEDIUM
                    else:
                        severity = Severity.LOW

                    # Age in days
                    age_days = (datetime.now(timezone.utc) - create_time).days

                    # Check if it looks like a dev/test instance
                    is_dev_test = any(keyword in db_instance_id.lower()
                                    for keyword in ['dev', 'test', 'staging', 'demo', 'sandbox'])

                    # Build recommendations
                    recommendations = []

                    if avg_cpu < 1.0 and avg_connections < 0.1:
                        if is_dev_test:
                            recommendations.append("Stop instance when not in use")
                            recommendations.append("Consider deleting if no longer needed")
                        else:
                            recommendations.append("Review if instance is still needed")

                    if avg_cpu < 2.0 and monthly_cost > 50:
                        # Suggest smaller instance type
                        smaller_class = self._suggest_smaller_instance_class(db_instance_class)
                        if smaller_class:
                            recommendations.append(f"Consider downsizing to {smaller_class}")

                    recommendations.append(f"CPU: {avg_cpu:.1f}%, Connections: {avg_connections:.1f}")
                    recommendation = "; ".join(recommendations)

                    # Safe to stop if dev/test and very low usage
                    safe_to_fix = (is_dev_test and avg_cpu < 1.0 and avg_connections < 0.1)

                    finding = Finding(
                        resource_id=db_instance_id,
                        resource_type="RDS Instance",
                        region=region,
                        monthly_cost=monthly_cost,
                        recommendation=recommendation,
                        severity=severity,
                        safe_to_fix=safe_to_fix,
                        fix_command=f"aws rds stop-db-instance --db-instance-identifier {db_instance_id} --region {region}" if safe_to_fix else None,
                        metadata={
                            "instance_class": db_instance_class,
                            "engine": engine,
                            "multi_az": multi_az,
                            "avg_cpu_percent": round(avg_cpu, 2),
                            "avg_connections": round(avg_connections, 2),
                            "hourly_cost": hourly_cost,
                            "age_days": age_days,
                            "is_dev_test": is_dev_test,
                            "cpu_threshold": self.cpu_threshold,
                            "connection_threshold": self.connection_threshold,
                        }
                    )
                    self._findings.append(finding)

            except Exception as e:
                print(f"Error scanning RDS instances in {region}: {e}")
                continue

        return self._findings

    def _get_average_cpu_utilization(self, cloudwatch, db_instance_id: str) -> float:
        """Get average CPU utilization over the monitoring period"""
        try:
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/RDS',
                MetricName='CPUUtilization',
                Dimensions=[
                    {'Name': 'DBInstanceIdentifier', 'Value': db_instance_id}
                ],
                StartTime=self.start_time,
                EndTime=self.end_time,
                Period=3600,  # 1-hour periods
                Statistics=['Average']
            )

            if not response['Datapoints']:
                return None

            return sum(point['Average'] for point in response['Datapoints']) / len(response['Datapoints'])

        except Exception as e:
            print(f"Error getting CPU metrics for RDS instance {db_instance_id}: {e}")
            return None

    def _get_average_connections(self, cloudwatch, db_instance_id: str) -> float:
        """Get average database connections over the monitoring period"""
        try:
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/RDS',
                MetricName='DatabaseConnections',
                Dimensions=[
                    {'Name': 'DBInstanceIdentifier', 'Value': db_instance_id}
                ],
                StartTime=self.start_time,
                EndTime=self.end_time,
                Period=3600,  # 1-hour periods
                Statistics=['Average']
            )

            if not response['Datapoints']:
                return None

            return sum(point['Average'] for point in response['Datapoints']) / len(response['Datapoints'])

        except Exception as e:
            print(f"Error getting connection metrics for RDS instance {db_instance_id}: {e}")
            return None

    def _suggest_smaller_instance_class(self, current_class: str) -> str:
        """Suggest a smaller instance class if available"""
        # Simple downsize suggestions
        downsizing_map = {
            'db.t3.small': 'db.t3.micro',
            'db.t3.medium': 'db.t3.small',
            'db.t3.large': 'db.t3.medium',
            'db.t3.xlarge': 'db.t3.large',
            'db.t3.2xlarge': 'db.t3.xlarge',
            'db.t4g.small': 'db.t4g.micro',
            'db.t4g.medium': 'db.t4g.small',
            'db.t4g.large': 'db.t4g.medium',
            'db.m5.xlarge': 'db.m5.large',
            'db.m5.2xlarge': 'db.m5.xlarge',
            'db.m5.4xlarge': 'db.m5.2xlarge',
            'db.r5.xlarge': 'db.r5.large',
            'db.r5.2xlarge': 'db.r5.xlarge',
        }

        return downsizing_map.get(current_class)

    def fix(self, finding: Finding, dry_run: bool = True) -> bool:
        if not finding.safe_to_fix:
            raise ValueError(f"RDS instance {finding.resource_id} is not marked safe to fix - manual review required")

        if dry_run:
            print(f"[DRY RUN] Would stop RDS instance {finding.resource_id}")
            return True

        rds = self.session.client('rds', region_name=finding.region)
        rds.stop_db_instance(DBInstanceIdentifier=finding.resource_id)
        print(f"Stopped RDS instance {finding.resource_id}")
        return True
