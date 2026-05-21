"""
Pattern 015: Over-Provisioned Lambda Memory
Detects Lambda functions with memory configured higher than needed

Lambda costs scale linearly with memory - 512MB costs 2x what 256MB does.
Many functions are left at default 1024MB when 128-256MB would suffice.
"""
from datetime import datetime, timedelta, timezone

from .base import BasePattern, Complexity, Finding, RemediationMode, RemediationResult, RiskTier, Category


class LambdaMemoryPattern(BasePattern):
    PATTERN_ID = "015"
    NAME = "Over-Provisioned Lambda Memory"
    DESCRIPTION = "Lambda functions with memory configured higher than actual usage"
    COMPLEXITY = Complexity.MEDIUM
    SERVICES = ["lambda", "cloudwatch"]
    CATEGORY = Category.COMPUTE
    REQUIRED_IAM = ["lambda:ListFunctions", "lambda:GetFunction", "cloudwatch:GetMetricStatistics", "ec2:DescribeRegions"]

    # Thresholds
    LOOKBACK_DAYS = 14
    MIN_INVOCATIONS = 100  # Need enough data to analyze
    MEMORY_HEADROOM = 1.3  # 30% headroom above max used
    MIN_MEMORY_SAVINGS_MB = 128  # Only report if savings > 128MB

    # Lambda pricing (us-east-1)
    # $0.0000166667 per GB-second
    PRICE_PER_GB_SECOND = 0.0000166667

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=self.LOOKBACK_DAYS)

        for region in regions:
            try:
                lambda_client = self.session.client("lambda", region_name=region)
                cloudwatch = self.session.client("cloudwatch", region_name=region)

                # Get all Lambda functions
                paginator = lambda_client.get_paginator("list_functions")
                for page in paginator.paginate():
                    for function in page.get("Functions", []):
                        self._check_function(
                            lambda_client, cloudwatch, function,
                            region, start_time, end_time
                        )

            except Exception as e:
                print(f"Error scanning Lambda in {region}: {e}")
                continue

        return self._findings

    def _check_function(self, lambda_client, cloudwatch, function: dict,
                        region: str, start_time: datetime, end_time: datetime):
        """Check if a Lambda function has over-provisioned memory."""
        function_name = function.get("FunctionName")
        function_arn = function.get("FunctionArn")
        configured_memory = function.get("MemorySize", 128)
        timeout = function.get("Timeout", 3)
        runtime = function.get("Runtime", "unknown")

        # Skip functions with very low memory (already optimized)
        if configured_memory <= 256:
            return

        # Get invocation count
        invocation_count = self._get_invocation_count(
            cloudwatch, function_name, start_time, end_time
        )

        # Skip functions with too few invocations
        if invocation_count is None or invocation_count < self.MIN_INVOCATIONS:
            return

        # Get max memory used
        max_memory_used = self._get_max_memory_used(
            cloudwatch, function_name, start_time, end_time
        )

        if max_memory_used is None:
            return

        # Calculate recommended memory (with headroom)
        recommended_memory = self._get_recommended_memory(max_memory_used)

        # Check if there's significant savings potential
        memory_savings = configured_memory - recommended_memory
        if memory_savings < self.MIN_MEMORY_SAVINGS_MB:
            return

        # Get average duration to calculate cost savings
        avg_duration_ms = self._get_avg_duration(
            cloudwatch, function_name, start_time, end_time
        )

        if avg_duration_ms is None:
            avg_duration_ms = 100  # Default assumption

        # Calculate monthly cost savings
        monthly_cost_current = self._calculate_monthly_cost(
            configured_memory, avg_duration_ms, invocation_count
        )
        monthly_cost_optimized = self._calculate_monthly_cost(
            recommended_memory, avg_duration_ms, invocation_count
        )
        monthly_savings = monthly_cost_current - monthly_cost_optimized

        # Skip trivial savings
        if monthly_savings < 1.0:
            return

        # Determine risk_tier
        savings_percent = (memory_savings / configured_memory) * 100
        if savings_percent > 75 and monthly_savings > 50:
            risk_tier= RiskTier.HIGH
        elif savings_percent > 50 or monthly_savings > 20:
            risk_tier= RiskTier.MEDIUM
        else:
            risk_tier= RiskTier.LOW

        summary= (
            f"Lambda '{function_name}' has {configured_memory}MB configured but only uses "
            f"~{max_memory_used:.0f}MB max. Recommend {recommended_memory}MB "
            f"(saves ${monthly_savings:.2f}/mo, {savings_percent:.0f}% reduction)."
        )

        finding = Finding(
            pattern_id=self.PATTERN_ID,
            resource_id=function_arn,
            resource_type="Lambda Function",
            region=region,
            monthly_impact_usd=monthly_savings,
            summary=summary,
            risk_tier=risk_tier,
            safe_to_fix=True,  # Memory changes are reversible
            fix_command=f"aws lambda update-function-configuration --function-name {function_name} --memory-size {recommended_memory} --region {region}",
            metadata={
                "function_name": function_name,
                "runtime": runtime,
                "configured_memory_mb": configured_memory,
                "max_memory_used_mb": max_memory_used,
                "recommended_memory_mb": recommended_memory,
                "memory_savings_mb": memory_savings,
                "savings_percent": savings_percent,
                "invocations_14d": invocation_count,
                "avg_duration_ms": avg_duration_ms,
                "timeout_seconds": timeout,
                "current_monthly_cost": monthly_cost_current,
                "optimized_monthly_cost": monthly_cost_optimized,
            }
        )
        self._findings.append(finding)

    def _get_invocation_count(self, cloudwatch, function_name: str,
                               start_time: datetime, end_time: datetime) -> int | None:
        """Get total invocation count."""
        try:
            response = cloudwatch.get_metric_statistics(
                Namespace="AWS/Lambda",
                MetricName="Invocations",
                Dimensions=[{"Name": "FunctionName", "Value": function_name}],
                StartTime=start_time,
                EndTime=end_time,
                Period=86400,  # Daily
                Statistics=["Sum"],
            )

            data_points = response.get("Datapoints", [])
            if not data_points:
                return None

            return int(sum(dp["Sum"] for dp in data_points))
        except Exception:
            return None

    def _get_max_memory_used(self, cloudwatch, function_name: str,
                              start_time: datetime, end_time: datetime) -> float | None:
        """Get maximum memory used by the function."""
        try:
            # Try Lambda Insights metrics first (more accurate)
            response = cloudwatch.get_metric_statistics(
                Namespace="LambdaInsights",
                MetricName="memory_utilization",
                Dimensions=[{"Name": "function_name", "Value": function_name}],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=["Maximum"],
            )

            data_points = response.get("Datapoints", [])
            if data_points:
                # Lambda Insights returns percentage
                max_percent = max(dp["Maximum"] for dp in data_points)
                # We need to know configured memory to convert, handled below
                return None  # Fall through to regular metrics

            # Fall back to standard metrics (max_memory_used)
            # Note: This requires enhanced monitoring
            response = cloudwatch.get_metric_statistics(
                Namespace="AWS/Lambda",
                MetricName="MaxMemoryUsed",
                Dimensions=[{"Name": "FunctionName", "Value": function_name}],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=["Maximum"],
            )

            data_points = response.get("Datapoints", [])
            if data_points:
                # Returns MB
                return max(dp["Maximum"] for dp in data_points)

            return None
        except Exception:
            return None

    def _get_avg_duration(self, cloudwatch, function_name: str,
                          start_time: datetime, end_time: datetime) -> float | None:
        """Get average duration in milliseconds."""
        try:
            response = cloudwatch.get_metric_statistics(
                Namespace="AWS/Lambda",
                MetricName="Duration",
                Dimensions=[{"Name": "FunctionName", "Value": function_name}],
                StartTime=start_time,
                EndTime=end_time,
                Period=86400,
                Statistics=["Average"],
            )

            data_points = response.get("Datapoints", [])
            if not data_points:
                return None

            total = sum(dp["Average"] for dp in data_points)
            return total / len(data_points)
        except Exception:
            return None

    def _get_recommended_memory(self, max_used_mb: float) -> int:
        """Get recommended memory size with headroom."""
        # Add 30% headroom
        needed = max_used_mb * self.MEMORY_HEADROOM

        # Round up to nearest Lambda memory tier
        # Lambda supports: 128, 256, 512, 1024, 1536, 2048, 3008, etc.
        # Actually supports any value from 128 to 10240 in 1MB increments
        # But we recommend standard tiers for simplicity
        tiers = [128, 256, 512, 1024, 1536, 2048, 3008, 4096, 5120, 6144, 7168, 8192, 9216, 10240]

        for tier in tiers:
            if tier >= needed:
                return tier

        return 10240  # Max

    def _calculate_monthly_cost(self, memory_mb: int, avg_duration_ms: float,
                                 invocations: int) -> float:
        """Calculate monthly Lambda cost."""
        # Extrapolate invocations to monthly (assume 14-day data)
        monthly_invocations = invocations * (30 / self.LOOKBACK_DAYS)

        # GB-seconds per invocation
        memory_gb = memory_mb / 1024
        duration_seconds = avg_duration_ms / 1000

        # Total GB-seconds per month
        total_gb_seconds = memory_gb * duration_seconds * monthly_invocations

        # Cost (first 400,000 GB-seconds free, but we're looking at relative cost)
        return total_gb_seconds * self.PRICE_PER_GB_SECOND

    def remediate(self, finding: Finding, mode: RemediationMode) -> RemediationResult:
        if mode != RemediationMode.API_CALL:
            return super().remediate(finding, mode)
        if not finding.safe_to_fix:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=False,
                message=f"Cannot safely fix {finding.resource_id}",
            )
        try:
            """Apply memory optimization."""
        

            recommended_memory = finding.metadata.get("recommended_memory_mb")
            function_name = finding.metadata.get("function_name")
            region = finding.region


            try:
                lambda_client = self.session.client("lambda", region_name=region)
                lambda_client.update_function_configuration(
                    FunctionName=function_name,
                    MemorySize=recommended_memory
                )
                print(f"Updated {function_name} memory to {recommended_memory}MB")
            except Exception as e:
                print(f"Error updating {function_name}: {e}")
                return False
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=True,
                message="updated Lambda memory",
            )
        except Exception as e:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=False,
                message=str(e),
            )
