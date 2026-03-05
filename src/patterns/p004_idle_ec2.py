"""
Pattern 004: Idle EC2 Instances
Detects EC2 instances with <5% average CPU utilization over 14 days
"""
from datetime import datetime, timedelta, timezone

from .base import BasePattern, Complexity, Finding, Severity


class IdleEC2Pattern(BasePattern):
    PATTERN_ID = "004"
    NAME = "Idle EC2 Instances"
    DESCRIPTION = "EC2 instances with <5% CPU average over 14 days (candidate for stopping or rightsizing)"
    COMPLEXITY = Complexity.MEDIUM
    SERVICES = ["ec2", "cloudwatch"]

    # CPU threshold for idle
    CPU_THRESHOLD = 5.0  # 5% CPU
    LOOKBACK_DAYS = 14

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        # Calculate time range
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=self.LOOKBACK_DAYS)

        for region in regions:
            try:
                ec2 = self.session.client("ec2", region_name=region)
                cloudwatch = self.session.client("cloudwatch", region_name=region)

                # Get all running instances
                paginator = ec2.get_paginator("describe_instances")
                instances = []
                for page in paginator.paginate(
                    Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
                ):
                    for reservation in page["Reservations"]:
                        instances.extend(reservation["Instances"])

                for instance in instances:
                    instance_id = instance["InstanceId"]
                    instance_type = instance["InstanceType"]
                    platform = instance.get("Platform", "Linux/UNIX")  # Windows or Linux
                    launch_time = instance.get("LaunchTime")
                    tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
                    name = tags.get("Name", "N/A")

                    # Skip if instance is less than 14 days old (avoid false positives for new instances)
                    if launch_time and (datetime.now(timezone.utc) - launch_time.replace(tzinfo=timezone.utc)).days < self.LOOKBACK_DAYS:
                        continue

                    # Get CPU utilization metrics
                    avg_cpu = self._get_avg_cpu(cloudwatch, instance_id, start_time, end_time)

                    if avg_cpu is None or avg_cpu >= self.CPU_THRESHOLD:
                        continue

                    # Calculate monthly cost estimate
                    monthly_cost = self._get_instance_monthly_cost(instance_type, region)

                    # Determine severity
                    if avg_cpu < 1.0 and monthly_cost > 100:
                        severity = Severity.CRITICAL
                    elif avg_cpu < 2.0:
                        severity = Severity.HIGH
                    elif avg_cpu < 5.0:
                        severity = Severity.MEDIUM
                    else:
                        severity = Severity.LOW

                    # Build recommendation
                    recommendation = (
                        f"EC2 instance has {avg_cpu:.1f}% CPU over {self.LOOKBACK_DAYS} days. "
                        f"Consider stopping, terminating, or downsizing. "
                        f"Instance: {instance_type}, Name: {name}"
                    )

                    # Check if instance has a purpose tag
                    purpose_tags = ["purpose", "environment", "env", "stack"]
                    has_purpose = any(t.lower() in tags for t in purpose_tags)

                    finding = Finding(
                        resource_id=instance_id,
                        resource_type="EC2 Instance",
                        region=region,
                        monthly_cost=monthly_cost,
                        recommendation=recommendation,
                        severity=severity,
                        safe_to_fix=False,  # Stopping instances should be manual
                        fix_command=f"aws ec2 stop-instances --instance-ids {instance_id} --region {region}",
                        metadata={
                            "instance_type": instance_type,
                            "name": name,
                            "avg_cpu_14d": avg_cpu,
                            "platform": platform,
                            "has_purpose_tag": has_purpose,
                            "tags": tags,
                            "launch_time": launch_time.isoformat() if launch_time else None,
                        }
                    )
                    self._findings.append(finding)

            except Exception as e:
                print(f"Error scanning {region}: {e}")
                continue

        return self._findings

    def _get_avg_cpu(self, cloudwatch, instance_id: str, start_time: datetime, end_time: datetime) -> float | None:
        """Get average CPU utilization for an instance."""
        try:
            response = cloudwatch.get_metric_statistics(
                Namespace="AWS/EC2",
                MetricName="CPUUtilization",
                Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,  # 1 hour granularity
                Statistics=["Average"],
            )

            data_points = response.get("Datapoints", [])
            if not data_points:
                return None

            # Calculate overall average
            total = sum(dp["Average"] for dp in data_points)
            return total / len(data_points)

        except Exception:
            # Instance might not have CloudWatch enabled
            return None

    def _get_instance_monthly_cost(self, instance_type: str, region: str) -> float:
        """Get estimated hourly cost for an instance type."""
        # Simplified pricing estimates (on-demand, varies by region)
        # These are approximate prices for us-east-1
        hourly_costs = {
            "t2.nano": 0.0058,
            "t2.micro": 0.0116,
            "t2.small": 0.023,
            "t2.medium": 0.0464,
            "t2.large": 0.0928,
            "t2.xlarge": 0.1856,
            "t2.2xlarge": 0.3712,
            "t3.nano": 0.0052,
            "t3.micro": 0.0104,
            "t3.small": 0.0208,
            "t3.medium": 0.0416,
            "t3.large": 0.0832,
            "t3.xlarge": 0.1664,
            "t3.2xlarge": 0.3328,
            "t3a.nano": 0.0048,
            "t3a.micro": 0.0096,
            "t3a.small": 0.0188,
            "t3a.medium": 0.0376,
            "t3a.large": 0.0752,
            "t3a.xlarge": 0.1504,
            "t3a.2xlarge": 0.3008,
            "m5.large": 0.096,
            "m5.xlarge": 0.192,
            "m5.2xlarge": 0.384,
            "m5.4xlarge": 0.768,
            "m5.8xlarge": 1.536,
            "m5.12xlarge": 2.304,
            "m5.16xlarge": 3.072,
            "m5.24xlarge": 4.608,
            "m5a.large": 0.086,
            "m5a.xlarge": 0.172,
            "m5a.2xlarge": 0.344,
            "m6i.large": 0.0864,
            "m6i.xlarge": 0.1728,
            "m6i.2xlarge": 0.3456,
            "c5.large": 0.085,
            "c5.xlarge": 0.17,
            "c5.2xlarge": 0.34,
            "c5.4xlarge": 0.68,
            "c5.9xlarge": 1.53,
            "c6i.large": 0.085,
            "c6i.xlarge": 0.17,
            "c6i.2xlarge": 0.34,
            "r5.large": 0.126,
            "r5.xlarge": 0.252,
            "r5.2xlarge": 0.504,
            "r6i.large": 0.128,
            "r6i.xlarge": 0.256,
        }

        return hourly_costs.get(instance_type, 0.10)  # Default fallback
