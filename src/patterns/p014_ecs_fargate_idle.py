"""
Pattern 014: Idle ECS/Fargate Tasks
Detects ECS services with zero traffic or Fargate tasks left running idle

Fargate costs ~$0.04-0.10/vCPU-hour, and idle services are often forgotten
after deployments or testing.
"""
import logging
from datetime import datetime, timedelta, timezone

from .base import BasePattern, Complexity, Finding, RemediationMode, RemediationResult, RiskTier, Category


logger = logging.getLogger(__name__)


class ECSFargateIdlePattern(BasePattern):
    PATTERN_ID = "014"
    NAME = "Idle ECS/Fargate Tasks"
    DESCRIPTION = "ECS services with zero traffic or idle Fargate tasks (forgotten deployments)"
    COMPLEXITY = Complexity.MEDIUM
    SERVICES = ["ecs", "cloudwatch"]
    CATEGORY = Category.COMPUTE
    REQUIRED_IAM = ["ecs:ListClusters", "ecs:ListServices", "ecs:DescribeServices", "cloudwatch:GetMetricStatistics", "ec2:DescribeRegions"]

    # Thresholds
    LOOKBACK_DAYS = 7
    REQUEST_THRESHOLD = 0  # Zero requests = idle
    CPU_THRESHOLD = 1.0  # <1% CPU average = idle

    # Fargate pricing (per hour, us-east-1)
    # Actual costs vary by region
    VCPU_HOUR_COST = 0.04048  # per vCPU-hour
    GB_HOUR_COST = 0.004445  # per GB-hour

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=self.LOOKBACK_DAYS)

        for region in regions:
            try:
                ecs = self.session.client("ecs", region_name=region)
                cloudwatch = self.session.client("cloudwatch", region_name=region)

                # Get all ECS clusters
                clusters = []
                paginator = ecs.get_paginator("list_clusters")
                for page in paginator.paginate():
                    clusters.extend(page.get("clusterArns", []))

                for cluster_arn in clusters:
                    cluster_name = cluster_arn.split("/")[-1]
                    self._scan_cluster(
                        ecs, cloudwatch, cluster_arn, cluster_name,
                        region, start_time, end_time
                    )

            except Exception:
                logger.exception("p014 error scanning ECS in region %s", region)
                continue

        return self._findings

    def _scan_cluster(self, ecs, cloudwatch, cluster_arn: str, cluster_name: str,
                      region: str, start_time: datetime, end_time: datetime):
        """Scan a single ECS cluster for idle services/tasks."""
        
        # Get all services in cluster
        services = []
        paginator = ecs.get_paginator("list_services")
        for page in paginator.paginate(cluster=cluster_arn):
            services.extend(page.get("serviceArns", []))

        if not services:
            return

        # Describe services in batches of 10 (API limit)
        for i in range(0, len(services), 10):
            batch = services[i:i + 10]
            try:
                response = ecs.describe_services(cluster=cluster_arn, services=batch)
                for service in response.get("services", []):
                    self._check_service(
                        ecs, cloudwatch, service, cluster_name,
                        region, start_time, end_time
                    )
            except Exception:
                logger.exception("p014 error describing services")
                continue

    def _check_service(self, ecs, cloudwatch, service: dict, cluster_name: str,
                       region: str, start_time: datetime, end_time: datetime):
        """Check if an ECS service is idle."""
        service_name = service.get("serviceName")
        service_arn = service.get("serviceArn")
        running_count = service.get("runningCount", 0)
        desired_count = service.get("desiredCount", 0)
        launch_type = service.get("launchType", "EC2")

        # Skip services with no running tasks
        if running_count == 0:
            return

        # Get task definition to calculate costs
        task_def_arn = service.get("taskDefinition")
        task_cpu, task_memory = self._get_task_resources(ecs, task_def_arn)

        # Get CPU utilization metrics
        avg_cpu = self._get_service_cpu(
            cloudwatch, cluster_name, service_name, start_time, end_time
        )

        # Check request count (if service has a load balancer)
        request_count = self._get_request_count(
            cloudwatch, service, region, start_time, end_time
        )

        # Determine if idle
        is_idle = False
        idle_reason = []

        if avg_cpu is not None and avg_cpu < self.CPU_THRESHOLD:
            is_idle = True
            idle_reason.append(f"CPU {avg_cpu:.2f}%")

        if request_count is not None and request_count == 0:
            is_idle = True
            idle_reason.append("zero requests")

        if not is_idle:
            return

        # Calculate monthly cost
        monthly_impact_usd= self._calculate_monthly_cost(
            task_cpu, task_memory, running_count, launch_type
        )

        # Skip trivial costs
        if monthly_impact_usd < 5.0:
            return

        # Determine risk_tier
        if monthly_impact_usd > 200:
            risk_tier= RiskTier.HIGH
        elif monthly_impact_usd > 50:
            risk_tier= RiskTier.MEDIUM
        else:
            risk_tier= RiskTier.LOW

        reason_str = ", ".join(idle_reason) if idle_reason else "low activity"
        summary= (
            f"ECS service '{service_name}' appears idle ({reason_str}) for {self.LOOKBACK_DAYS} days. "
            f"Running {running_count} tasks ({launch_type}). "
            f"Consider scaling to 0 or deleting if no longer needed."
        )

        finding = Finding(
            pattern_id=self.PATTERN_ID,
            resource_id=service_arn,
            resource_type="ECS Service",
            region=region,
            monthly_impact_usd=monthly_impact_usd,
            summary=summary,
            risk_tier=risk_tier,
            safe_to_fix=False,  # Scaling down services requires human judgment
            fix_command=f"aws ecs update-service --cluster {cluster_name} --service {service_name} --desired-count 0 --region {region}",
            metadata={
                "cluster_name": cluster_name,
                "service_name": service_name,
                "launch_type": launch_type,
                "running_count": running_count,
                "desired_count": desired_count,
                "avg_cpu_7d": avg_cpu,
                "request_count_7d": request_count,
                "task_cpu": task_cpu,
                "task_memory_mb": task_memory,
                "idle_reason": idle_reason,
            }
        )
        self._findings.append(finding)

    def _get_task_resources(self, ecs, task_def_arn: str) -> tuple[float, float]:
        """Get CPU and memory from task definition."""
        try:
            response = ecs.describe_task_definition(taskDefinition=task_def_arn)
            task_def = response.get("taskDefinition", {})
            
            # Fargate uses cpu/memory at task level
            cpu = task_def.get("cpu", "256")
            memory = task_def.get("memory", "512")
            
            # CPU is in units (256 = 0.25 vCPU, 1024 = 1 vCPU)
            cpu_vcpu = float(cpu) / 1024
            memory_mb = float(memory)
            
            return cpu_vcpu, memory_mb
        except Exception:
            return 0.25, 512  # Default: 0.25 vCPU, 512MB

    def _get_service_cpu(self, cloudwatch, cluster_name: str, service_name: str,
                         start_time: datetime, end_time: datetime) -> float | None:
        """Get average CPU utilization for a service."""
        try:
            response = cloudwatch.get_metric_statistics(
                Namespace="AWS/ECS",
                MetricName="CPUUtilization",
                Dimensions=[
                    {"Name": "ClusterName", "Value": cluster_name},
                    {"Name": "ServiceName", "Value": service_name}
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,  # 1-hour granularity
                Statistics=["Average"],
            )
            
            data_points = response.get("Datapoints", [])
            if not data_points:
                return None
                
            total = sum(dp["Average"] for dp in data_points)
            return total / len(data_points)
        except Exception:
            return None

    def _get_request_count(self, cloudwatch, service: dict, region: str,
                           start_time: datetime, end_time: datetime) -> int | None:
        """Get request count from ALB if service has one attached."""
        load_balancers = service.get("loadBalancers", [])
        if not load_balancers:
            return None  # No LB, can't determine request count

        try:
            # Get target group ARN
            target_group_arn = load_balancers[0].get("targetGroupArn")
            if not target_group_arn:
                return None

            # Extract target group name from ARN
            # arn:aws:elasticloadbalancing:region:account:targetgroup/name/id
            tg_parts = target_group_arn.split("/")
            if len(tg_parts) >= 2:
                tg_name = "/".join(tg_parts[-2:])  # targetgroup/name/id portion
            else:
                return None

            response = cloudwatch.get_metric_statistics(
                Namespace="AWS/ApplicationELB",
                MetricName="RequestCount",
                Dimensions=[
                    {"Name": "TargetGroup", "Value": f"targetgroup/{tg_name}"}
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=86400,  # Daily granularity
                Statistics=["Sum"],
            )

            data_points = response.get("Datapoints", [])
            if not data_points:
                return 0

            return int(sum(dp["Sum"] for dp in data_points))
        except Exception:
            return None

    def _calculate_monthly_cost(self, vcpu: float, memory_mb: float,
                                 task_count: int, launch_type: str) -> float:
        """Calculate monthly cost for running tasks."""
        if launch_type != "FARGATE":
            # For EC2 launch type, cost is harder to attribute
            # Return estimate based on resource usage
            return task_count * vcpu * 20  # Rough estimate

        # Fargate pricing
        hours_per_month = 730
        memory_gb = memory_mb / 1024

        vcpu_cost = vcpu * self.VCPU_HOUR_COST * hours_per_month
        memory_cost = memory_gb * self.GB_HOUR_COST * hours_per_month

        return (vcpu_cost + memory_cost) * task_count
