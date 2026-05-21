"""
Pattern 016: Unused API Gateway Stages
Detects API Gateway stages with zero requests

API Gateway stages incur costs even with zero traffic due to cache and
other features. Forgotten dev/test stages accumulate charges.
"""
from datetime import datetime, timedelta, timezone

from .base import BasePattern, Complexity, Finding, RemediationMode, RemediationResult, RiskTier, Category


class APIGWUnusedStagesPattern(BasePattern):
    PATTERN_ID = "016"
    NAME = "Unused API Gateway Stages"
    DESCRIPTION = "API Gateway stages with zero requests (forgotten dev/test deployments)"
    COMPLEXITY = Complexity.EASY
    SERVICES = ["apigateway", "apigatewayv2", "cloudwatch"]
    CATEGORY = Category.NETWORK
    REQUIRED_IAM = ["apigateway:GET", "cloudwatch:GetMetricStatistics", "ec2:DescribeRegions"]

    # Thresholds
    LOOKBACK_DAYS = 30  # Longer lookback for API stages
    
    # API Gateway pricing (us-east-1)
    # REST API: $3.50 per million requests (first 333M)
    # HTTP API: $1.00 per million requests (first 300M)
    # WebSocket: $1.00 per million messages
    # Cache: $0.02-$0.20 per hour depending on size
    CACHE_HOURLY_COST = {
        "0.5": 0.02,
        "1.6": 0.038,
        "6.1": 0.20,
        "13.5": 0.25,
        "28.4": 0.50,
        "58.2": 1.00,
        "118": 1.90,
        "237": 3.80,
    }

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=self.LOOKBACK_DAYS)

        for region in regions:
            try:
                # Scan REST APIs (v1)
                self._scan_rest_apis(region, start_time, end_time)
                
                # Scan HTTP APIs (v2)
                self._scan_http_apis(region, start_time, end_time)

            except Exception as e:
                print(f"Error scanning API Gateway in {region}: {e}")
                continue

        return self._findings

    def _scan_rest_apis(self, region: str, start_time: datetime, end_time: datetime):
        """Scan REST API (v1) stages."""
        apigw = self.session.client("apigateway", region_name=region)
        cloudwatch = self.session.client("cloudwatch", region_name=region)

        # Get all REST APIs
        apis = []
        paginator = apigw.get_paginator("get_rest_apis")
        for page in paginator.paginate():
            apis.extend(page.get("items", []))

        for api in apis:
            api_id = api.get("id")
            api_name = api.get("name", "unnamed")
            
            try:
                # Get stages for this API
                stages_response = apigw.get_stages(restApiId=api_id)
                stages = stages_response.get("item", [])
                
                for stage in stages:
                    self._check_rest_stage(
                        cloudwatch, api_id, api_name, stage,
                        region, start_time, end_time
                    )
            except Exception as e:
                print(f"Error getting stages for {api_name}: {e}")
                continue

    def _check_rest_stage(self, cloudwatch, api_id: str, api_name: str,
                          stage: dict, region: str,
                          start_time: datetime, end_time: datetime):
        """Check if a REST API stage is unused."""
        stage_name = stage.get("stageName")
        created_date = stage.get("createdDate")
        cache_cluster_enabled = stage.get("cacheClusterEnabled", False)
        cache_cluster_size = stage.get("cacheClusterSize", "0.5")

        # Get request count
        request_count = self._get_rest_api_requests(
            cloudwatch, api_name, stage_name, start_time, end_time
        )

        if request_count is None or request_count > 0:
            return  # Not unused

        # Calculate monthly cost (primarily cache if enabled)
        monthly_impact_usd= 0.0
        if cache_cluster_enabled:
            hourly_cost = self.CACHE_HOURLY_COST.get(cache_cluster_size, 0.02)
            monthly_impact_usd= hourly_cost * 730  # Hours per month

        # Even without cache, unused stages represent cleanup opportunity
        # But we only flag as findings if there's actual cost
        if monthly_impact_usd < 1.0 and not cache_cluster_enabled:
            # Still report but with lower risk_tier for cleanup purposes
            monthly_impact_usd= 0.0  # No direct cost, but clutters the account

        # Determine risk_tier
        if cache_cluster_enabled and monthly_impact_usd > 50:
            risk_tier= RiskTier.HIGH
        elif cache_cluster_enabled:
            risk_tier= RiskTier.MEDIUM
        else:
            risk_tier= RiskTier.LOW

        # Check if it's a common test/dev stage name
        test_indicators = ["dev", "test", "staging", "beta", "sandbox", "demo"]
        is_likely_test = any(ind in stage_name.lower() for ind in test_indicators)

        summary= (
            f"REST API stage '{api_name}/{stage_name}' has zero requests in {self.LOOKBACK_DAYS} days. "
        )
        if cache_cluster_enabled:
            summary += f"Cache enabled ({cache_cluster_size}GB) costs ${monthly_impact_usd:.2f}/mo. "
        if is_likely_test:
            summary += "Appears to be a test/dev stage. "
        summary += "Consider deleting if no longer needed."

        resource_id = f"arn:aws:apigateway:{region}::/restapis/{api_id}/stages/{stage_name}"

        finding = Finding(
            pattern_id=self.PATTERN_ID,
            resource_id=resource_id,
            resource_type="API Gateway Stage (REST)",
            region=region,
            monthly_impact_usd=monthly_impact_usd,
            summary=summary,
            risk_tier=risk_tier,
            safe_to_fix=is_likely_test,  # More cautious with prod-like names
            fix_command=f"aws apigateway delete-stage --rest-api-id {api_id} --stage-name {stage_name} --region {region}",
            metadata={
                "api_id": api_id,
                "api_name": api_name,
                "stage_name": stage_name,
                "api_type": "REST",
                "cache_enabled": cache_cluster_enabled,
                "cache_size_gb": cache_cluster_size if cache_cluster_enabled else None,
                "request_count_30d": request_count,
                "is_likely_test": is_likely_test,
                "created_date": created_date.isoformat() if created_date else None,
            }
        )
        self._findings.append(finding)

    def _get_rest_api_requests(self, cloudwatch, api_name: str, stage_name: str,
                                start_time: datetime, end_time: datetime) -> int | None:
        """Get request count for REST API stage."""
        try:
            response = cloudwatch.get_metric_statistics(
                Namespace="AWS/ApiGateway",
                MetricName="Count",
                Dimensions=[
                    {"Name": "ApiName", "Value": api_name},
                    {"Name": "Stage", "Value": stage_name}
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=86400,  # Daily
                Statistics=["Sum"],
            )

            data_points = response.get("Datapoints", [])
            if not data_points:
                return 0  # No data = no requests

            return int(sum(dp["Sum"] for dp in data_points))
        except Exception:
            return None

    def _scan_http_apis(self, region: str, start_time: datetime, end_time: datetime):
        """Scan HTTP APIs (v2) stages."""
        apigwv2 = self.session.client("apigatewayv2", region_name=region)
        cloudwatch = self.session.client("cloudwatch", region_name=region)

        # Get all HTTP/WebSocket APIs
        apis = []
        try:
            paginator = apigwv2.get_paginator("get_apis")
            for page in paginator.paginate():
                apis.extend(page.get("Items", []))
        except Exception as e:
            print(f"Error listing HTTP APIs in {region}: {e}")
            return

        for api in apis:
            api_id = api.get("ApiId")
            api_name = api.get("Name", "unnamed")
            protocol_type = api.get("ProtocolType", "HTTP")

            try:
                # Get stages
                stages = []
                paginator = apigwv2.get_paginator("get_stages")
                for page in paginator.paginate(ApiId=api_id):
                    stages.extend(page.get("Items", []))

                for stage in stages:
                    self._check_http_stage(
                        cloudwatch, api_id, api_name, stage,
                        protocol_type, region, start_time, end_time
                    )
            except Exception as e:
                print(f"Error getting stages for HTTP API {api_name}: {e}")
                continue

    def _check_http_stage(self, cloudwatch, api_id: str, api_name: str,
                          stage: dict, protocol_type: str, region: str,
                          start_time: datetime, end_time: datetime):
        """Check if an HTTP/WebSocket API stage is unused."""
        stage_name = stage.get("StageName")
        created_date = stage.get("CreatedDate")

        # Get request/message count
        request_count = self._get_http_api_requests(
            cloudwatch, api_id, stage_name, protocol_type, start_time, end_time
        )

        if request_count is None or request_count > 0:
            return

        # HTTP APIs have no cache, so cost is $0 when unused
        # But still worth flagging for cleanup
        monthly_impact_usd= 0.0

        # Determine risk_tier (low since no direct cost)
        risk_tier= RiskTier.LOW

        test_indicators = ["dev", "test", "staging", "beta", "sandbox", "demo", "$default"]
        is_likely_test = any(ind in stage_name.lower() for ind in test_indicators)

        summary= (
            f"{protocol_type} API stage '{api_name}/{stage_name}' has zero "
            f"{'messages' if protocol_type == 'WEBSOCKET' else 'requests'} in "
            f"{self.LOOKBACK_DAYS} days. Consider deleting if no longer needed."
        )

        resource_id = f"arn:aws:apigateway:{region}::/apis/{api_id}/stages/{stage_name}"

        finding = Finding(
            pattern_id=self.PATTERN_ID,
            resource_id=resource_id,
            resource_type=f"API Gateway Stage ({protocol_type})",
            region=region,
            monthly_impact_usd=monthly_impact_usd,
            summary=summary,
            risk_tier=risk_tier,
            safe_to_fix=is_likely_test,
            fix_command=f"aws apigatewayv2 delete-stage --api-id {api_id} --stage-name {stage_name} --region {region}",
            metadata={
                "api_id": api_id,
                "api_name": api_name,
                "stage_name": stage_name,
                "api_type": protocol_type,
                "request_count_30d": request_count,
                "is_likely_test": is_likely_test,
                "created_date": created_date.isoformat() if created_date else None,
            }
        )
        self._findings.append(finding)

    def _get_http_api_requests(self, cloudwatch, api_id: str, stage_name: str,
                                protocol_type: str, start_time: datetime,
                                end_time: datetime) -> int | None:
        """Get request/message count for HTTP/WebSocket API."""
        try:
            metric_name = "MessageCount" if protocol_type == "WEBSOCKET" else "Count"

            response = cloudwatch.get_metric_statistics(
                Namespace="AWS/ApiGateway",
                MetricName=metric_name,
                Dimensions=[
                    {"Name": "ApiId", "Value": api_id},
                    {"Name": "Stage", "Value": stage_name}
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=86400,
                Statistics=["Sum"],
            )

            data_points = response.get("Datapoints", [])
            if not data_points:
                return 0

            return int(sum(dp["Sum"] for dp in data_points))
        except Exception:
            return None
