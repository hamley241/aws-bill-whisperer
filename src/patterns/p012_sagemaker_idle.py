"""
Pattern 012: Idle SageMaker Resources
Detects SageMaker endpoints and notebook instances with zero or near-zero usage.

SageMaker costs:
- Endpoints: $1-10+/hour depending on instance type (24/7 = $720-7200+/month)
- Notebook instances: $0.50-5+/hour (24/7 = $360-3600+/month)

Common waste patterns:
- Dev/test endpoints left running
- Notebook instances forgotten after experiments
- Endpoints with no inference traffic
"""
import logging
from datetime import datetime, timedelta, timezone

from .base import BasePattern, Complexity, Finding, RemediationMode, RemediationResult, RiskTier, Category


logger = logging.getLogger(__name__)


class SageMakerIdlePattern(BasePattern):
    PATTERN_ID = "012"
    NAME = "Idle SageMaker Resources"
    DESCRIPTION = "SageMaker endpoints/notebooks with zero usage (expensive to leave running)"
    COMPLEXITY = Complexity.MEDIUM
    SERVICES = ["sagemaker", "cloudwatch"]
    CATEGORY = Category.ML
    REQUIRED_IAM = ["sagemaker:ListEndpoints", "sagemaker:ListNotebookInstances", "cloudwatch:GetMetricStatistics", "ec2:DescribeRegions"]

    # Lookback period for usage metrics
    LOOKBACK_DAYS = 7
    
    # Invocation threshold (per day average)
    MIN_INVOCATIONS_PER_DAY = 1
    
    # Hourly pricing estimates (approximate, varies by instance type)
    ENDPOINT_HOURLY_COSTS = {
        "ml.t2.medium": 0.065,
        "ml.t2.large": 0.130,
        "ml.t2.xlarge": 0.260,
        "ml.t2.2xlarge": 0.521,
        "ml.t3.medium": 0.058,
        "ml.t3.large": 0.117,
        "ml.t3.xlarge": 0.233,
        "ml.t3.2xlarge": 0.466,
        "ml.m4.xlarge": 0.28,
        "ml.m4.2xlarge": 0.56,
        "ml.m4.4xlarge": 1.12,
        "ml.m5.large": 0.134,
        "ml.m5.xlarge": 0.269,
        "ml.m5.2xlarge": 0.538,
        "ml.m5.4xlarge": 1.075,
        "ml.m5.12xlarge": 3.226,
        "ml.m5.24xlarge": 6.451,
        "ml.c4.xlarge": 0.279,
        "ml.c4.2xlarge": 0.557,
        "ml.c5.xlarge": 0.238,
        "ml.c5.2xlarge": 0.476,
        "ml.c5.4xlarge": 0.952,
        "ml.c5.9xlarge": 2.142,
        "ml.p2.xlarge": 1.26,
        "ml.p3.2xlarge": 4.284,
        "ml.p3.8xlarge": 17.136,
        "ml.g4dn.xlarge": 0.736,
        "ml.g4dn.2xlarge": 1.053,
        "ml.g4dn.4xlarge": 1.686,
        "ml.g4dn.8xlarge": 3.045,
        "ml.g5.xlarge": 1.408,
        "ml.g5.2xlarge": 1.691,
        "ml.g5.4xlarge": 2.258,
        "ml.inf1.xlarge": 0.297,
        "ml.inf1.2xlarge": 0.472,
        "ml.inf1.6xlarge": 1.416,
    }
    
    NOTEBOOK_HOURLY_COSTS = {
        "ml.t2.medium": 0.065,
        "ml.t2.large": 0.130,
        "ml.t2.xlarge": 0.260,
        "ml.t3.medium": 0.058,
        "ml.t3.large": 0.117,
        "ml.t3.xlarge": 0.233,
        "ml.t3.2xlarge": 0.466,
        "ml.m4.xlarge": 0.28,
        "ml.m4.2xlarge": 0.56,
        "ml.m5.xlarge": 0.269,
        "ml.m5.2xlarge": 0.538,
        "ml.m5.4xlarge": 1.075,
        "ml.c4.xlarge": 0.279,
        "ml.c5.xlarge": 0.238,
        "ml.c5.2xlarge": 0.476,
        "ml.p2.xlarge": 1.26,
        "ml.p3.2xlarge": 4.284,
        "ml.g4dn.xlarge": 0.736,
        "ml.g5.xlarge": 1.408,
    }

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                sagemaker = self.session.client("sagemaker", region_name=region)
                cloudwatch = self.session.client("cloudwatch", region_name=region)

                # Scan endpoints
                self._scan_endpoints(sagemaker, cloudwatch, region)
                
                # Scan notebook instances
                self._scan_notebooks(sagemaker, cloudwatch, region)

            except Exception:
                logger.exception("p012 error scanning region %s", region)
                continue

        return self._findings

    def _scan_endpoints(self, sagemaker, cloudwatch, region: str):
        """Scan SageMaker endpoints for idle resources."""
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=self.LOOKBACK_DAYS)
        
        try:
            paginator = sagemaker.get_paginator("list_endpoints")
            
            for page in paginator.paginate():
                for endpoint in page.get("Endpoints", []):
                    endpoint_name = endpoint["EndpointName"]
                    endpoint_status = endpoint["EndpointStatus"]
                    
                    # Only check InService endpoints
                    if endpoint_status != "InService":
                        continue
                    
                    # Get endpoint config for instance type
                    try:
                        endpoint_desc = sagemaker.describe_endpoint(EndpointName=endpoint_name)
                        config_name = endpoint_desc.get("EndpointConfigName")
                        
                        if config_name:
                            config = sagemaker.describe_endpoint_config(EndpointConfigName=config_name)
                            variants = config.get("ProductionVariants", [])
                        else:
                            variants = []
                    except Exception:
                        variants = []
                    
                    # Get invocation count
                    invocation_count = self._get_endpoint_invocations(
                        cloudwatch, endpoint_name, start_time, end_time
                    )
                    
                    # Calculate daily average
                    daily_avg = invocation_count / self.LOOKBACK_DAYS
                    
                    if daily_avg < self.MIN_INVOCATIONS_PER_DAY:
                        # Calculate monthly cost
                        monthly_impact_usd= self._calculate_endpoint_cost(variants)
                        
                        # Determine risk_tier based on cost
                        if monthly_impact_usd > 500:
                            risk_tier= RiskTier.HIGH
                        elif monthly_impact_usd > 100:
                            risk_tier= RiskTier.HIGH
                        else:
                            risk_tier= RiskTier.MEDIUM
                        
                        instance_types = [v.get("InstanceType", "unknown") for v in variants]
                        instance_counts = [v.get("InitialInstanceCount", 1) for v in variants]
                        
                        finding = Finding(
                            pattern_id=self.PATTERN_ID,
                            resource_id=endpoint_name,
                            resource_type="SageMaker Endpoint",
                            region=region,
                            monthly_impact_usd=monthly_impact_usd,
                            summary=f"Endpoint has {invocation_count} total invocations in {self.LOOKBACK_DAYS} days "
                                          f"(avg {daily_avg:.1f}/day). Consider deleting if not needed. "
                                          f"Instances: {instance_types}",
                            risk_tier=risk_tier,
                            safe_to_fix=False,  # Deleting endpoints should be manual
                            fix_command=f"aws sagemaker delete-endpoint --endpoint-name {endpoint_name} --region {region}",
                            metadata={
                                "endpoint_status": endpoint_status,
                                "invocations_7d": invocation_count,
                                "avg_invocations_per_day": round(daily_avg, 2),
                                "instance_types": instance_types,
                                "instance_counts": instance_counts,
                                "config_name": config_name if config_name else "unknown"
                            }
                        )
                        self._findings.append(finding)

        except Exception:
            logger.exception("p012 error scanning endpoints in region %s", region)

    def _scan_notebooks(self, sagemaker, cloudwatch, region: str):
        """Scan SageMaker notebook instances for idle resources."""
        try:
            paginator = sagemaker.get_paginator("list_notebook_instances")
            
            for page in paginator.paginate():
                for notebook in page.get("NotebookInstances", []):
                    notebook_name = notebook["NotebookInstanceName"]
                    instance_type = notebook.get("InstanceType", "ml.t3.medium")
                    status = notebook["NotebookInstanceStatus"]
                    
                    # Only check InService notebooks
                    if status != "InService":
                        continue
                    
                    # Get notebook details
                    try:
                        notebook_desc = sagemaker.describe_notebook_instance(
                            NotebookInstanceName=notebook_name
                        )
                        last_modified = notebook_desc.get("LastModifiedTime")
                        creation_time = notebook_desc.get("CreationTime")
                    except Exception:
                        last_modified = None
                        creation_time = None
                    
                    # Calculate how long it's been running without modification
                    if last_modified:
                        idle_days = (datetime.now(timezone.utc) - last_modified.replace(tzinfo=timezone.utc)).days
                    else:
                        idle_days = self.LOOKBACK_DAYS + 1  # Assume idle if we can't tell
                    
                    # Flag if idle for more than 7 days
                    if idle_days >= self.LOOKBACK_DAYS:
                        hourly_cost = self.NOTEBOOK_HOURLY_COSTS.get(instance_type, 0.50)
                        monthly_impact_usd= hourly_cost * 24 * 30
                        
                        # Determine risk_tier
                        if monthly_impact_usd > 500:
                            risk_tier= RiskTier.HIGH
                        elif monthly_impact_usd > 100:
                            risk_tier= RiskTier.HIGH
                        else:
                            risk_tier= RiskTier.MEDIUM
                        
                        finding = Finding(
                            pattern_id=self.PATTERN_ID,
                            resource_id=notebook_name,
                            resource_type="SageMaker Notebook Instance",
                            region=region,
                            monthly_impact_usd=monthly_impact_usd,
                            summary=f"Notebook instance has been idle for {idle_days} days. "
                                          f"Consider stopping (to save ~${monthly_impact_usd:.0f}/mo) or deleting. "
                                          f"Instance type: {instance_type}",
                            risk_tier=risk_tier,
                            safe_to_fix=False,
                            fix_command=f"aws sagemaker stop-notebook-instance --notebook-instance-name {notebook_name} --region {region}",
                            metadata={
                                "instance_type": instance_type,
                                "status": status,
                                "idle_days": idle_days,
                                "last_modified": last_modified.isoformat() if last_modified else None,
                                "creation_time": creation_time.isoformat() if creation_time else None,
                                "hourly_cost": hourly_cost
                            }
                        )
                        self._findings.append(finding)

        except Exception:
            logger.exception("p012 error scanning notebooks in region %s", region)

    def _get_endpoint_invocations(self, cloudwatch, endpoint_name: str, start_time: datetime, end_time: datetime) -> int:
        """Get total invocation count for an endpoint."""
        try:
            response = cloudwatch.get_metric_statistics(
                Namespace="AWS/SageMaker",
                MetricName="Invocations",
                Dimensions=[
                    {"Name": "EndpointName", "Value": endpoint_name},
                    {"Name": "VariantName", "Value": "AllTraffic"}
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600 * 24,  # Daily granularity
                Statistics=["Sum"],
            )
            
            data_points = response.get("Datapoints", [])
            total = sum(dp.get("Sum", 0) for dp in data_points)
            return int(total)
            
        except Exception:
            # Try without VariantName dimension
            try:
                response = cloudwatch.get_metric_statistics(
                    Namespace="AWS/SageMaker",
                    MetricName="Invocations",
                    Dimensions=[
                        {"Name": "EndpointName", "Value": endpoint_name}
                    ],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=3600 * 24,
                    Statistics=["Sum"],
                )
                
                data_points = response.get("Datapoints", [])
                total = sum(dp.get("Sum", 0) for dp in data_points)
                return int(total)
            except Exception:
                return 0

    def _calculate_endpoint_cost(self, variants: list[dict]) -> float:
        """Calculate monthly cost for endpoint variants."""
        total_hourly = 0.0
        
        for variant in variants:
            instance_type = variant.get("InstanceType", "ml.m5.xlarge")
            instance_count = variant.get("InitialInstanceCount", 1)
            hourly_cost = self.ENDPOINT_HOURLY_COSTS.get(instance_type, 1.0)
            total_hourly += hourly_cost * instance_count
        
        # Default to at least 1 instance if no variants
        if not variants:
            total_hourly = 1.0
        
        # Convert to monthly (24 hours × 30 days)
        return total_hourly * 24 * 30

    def remediate(self, finding: Finding, mode: RemediationMode) -> RemediationResult:
        if mode != RemediationMode.API_CALL:
            return super().remediate(finding, mode)
        if not finding.safe_to_fix:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=False,
                message=f"Cannot safely fix {finding.resource_id}. "
                f"Deleting/stopping SageMaker resources requires manual confirmation.",
            )
        try:
            """Apply fix for idle SageMaker resources."""
        
        
        
            sagemaker = self.session.client("sagemaker", region_name=finding.region)
        
            try:
                if finding.resource_type == "SageMaker Endpoint":
                    sagemaker.delete_endpoint(EndpointName=finding.resource_id)
                elif finding.resource_type == "SageMaker Notebook Instance":
                    sagemaker.stop_notebook_instance(NotebookInstanceName=finding.resource_id)
            except Exception:
                logger.exception("p012 error fixing %s", finding.resource_id)
                return False
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=True,
                message="stopped/deleted SageMaker resource",
            )
        except Exception as e:
            return RemediationResult(
                finding_id=finding.id,
                pattern_id=self.PATTERN_ID,
                mode=mode,
                success=False,
                message=str(e),
            )
