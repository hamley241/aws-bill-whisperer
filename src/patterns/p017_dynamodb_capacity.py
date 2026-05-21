"""
Pattern 017: DynamoDB Capacity Mode Optimization
Detects DynamoDB tables on suboptimal capacity mode:
- On-Demand when Provisioned would be cheaper (predictable traffic)
- Provisioned with <10% utilization (overprovisioned)
"""

from datetime import datetime, timedelta, timezone

from .base import BasePattern, Complexity, Finding, RemediationMode, RemediationResult, RiskTier, Category


class DynamoDBCapacityPattern(BasePattern):
    PATTERN_ID = "017"
    NAME = "DynamoDB Capacity Mode Optimization"
    DESCRIPTION = "DynamoDB tables on wrong capacity mode (On-Demand vs Provisioned)"
    COMPLEXITY = Complexity.MEDIUM
    SERVICES = ["dynamodb"]
    CATEGORY = Category.DATABASE
    REQUIRED_IAM = ["dynamodb:ListTables", "dynamodb:DescribeTable", "cloudwatch:GetMetricStatistics", "ec2:DescribeRegions"]

    # Pricing (us-east-1, approximate)
    # On-Demand: $1.25 per million WCUs, $0.25 per million RCUs
    # Provisioned: $0.00065 per WCU-hour, $0.00013 per RCU-hour
    ON_DEMAND_WCU_PRICE = 1.25 / 1_000_000  # per WCU
    ON_DEMAND_RCU_PRICE = 0.25 / 1_000_000  # per RCU
    PROVISIONED_WCU_HOUR = 0.00065
    PROVISIONED_RCU_HOUR = 0.00013

    # Thresholds
    UTILIZATION_THRESHOLD = 0.10  # 10% utilization = overprovisioned
    STEADY_TRAFFIC_THRESHOLD = 0.15  # CV < 0.15 = steady traffic (good for provisioned)
    MIN_REQUESTS_FOR_ANALYSIS = 1000  # Minimum requests/day to analyze

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                dynamodb = self.session.client('dynamodb', region_name=region)
                cloudwatch = self.session.client('cloudwatch', region_name=region)

                # List all tables
                paginator = dynamodb.get_paginator('list_tables')
                for page in paginator.paginate():
                    for table_name in page.get('TableNames', []):
                        self._analyze_table(dynamodb, cloudwatch, table_name, region)

            except Exception as e:
                print(f"Error scanning DynamoDB in {region}: {e}")
                continue

        return self._findings

    def _analyze_table(self, dynamodb, cloudwatch, table_name: str, region: str):
        """Analyze a single DynamoDB table for capacity optimization."""
        try:
            # Get table description
            table = dynamodb.describe_table(TableName=table_name)['Table']
            billing_mode = table.get('BillingModeSummary', {}).get('BillingMode', 'PROVISIONED')

            # Get CloudWatch metrics for the last 7 days
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(days=7)

            if billing_mode == 'PAY_PER_REQUEST':  # On-Demand
                self._check_on_demand_table(cloudwatch, table, table_name, region, start_time, end_time)
            else:  # Provisioned
                self._check_provisioned_table(cloudwatch, table, table_name, region, start_time, end_time)

        except Exception as e:
            print(f"Error analyzing table {table_name}: {e}")

    def _check_on_demand_table(self, cloudwatch, table, table_name: str, region: str,
                                start_time: datetime, end_time: datetime):
        """Check if On-Demand table would be cheaper as Provisioned."""
        # Get consumed capacity metrics
        consumed_read = self._get_metric_stats(
            cloudwatch, table_name, 'ConsumedReadCapacityUnits', start_time, end_time
        )
        consumed_write = self._get_metric_stats(
            cloudwatch, table_name, 'ConsumedWriteCapacityUnits', start_time, end_time
        )

        if not consumed_read['datapoints'] or not consumed_write['datapoints']:
            return  # No data to analyze

        # Calculate average and coefficient of variation
        avg_rcu = consumed_read['average']
        avg_wcu = consumed_write['average']
        cv_rcu = consumed_read['cv']
        cv_wcu = consumed_write['cv']

        # Check if traffic is steady (good candidate for provisioned)
        is_steady = cv_rcu < self.STEADY_TRAFFIC_THRESHOLD and cv_wcu < self.STEADY_TRAFFIC_THRESHOLD

        if not is_steady:
            return  # Traffic is variable, On-Demand is probably fine

        # Calculate costs
        # On-Demand cost (current)
        total_rcu = consumed_read['sum']
        total_wcu = consumed_write['sum']
        weekly_on_demand_cost = (total_rcu * self.ON_DEMAND_RCU_PRICE +
                                  total_wcu * self.ON_DEMAND_WCU_PRICE)
        monthly_on_demand_cost = weekly_on_demand_cost * 4.33

        # Provisioned cost (estimated with 20% buffer)
        hours_per_week = 168
        provisioned_rcu = int(avg_rcu * 1.2)  # 20% buffer
        provisioned_wcu = int(avg_wcu * 1.2)
        weekly_provisioned_cost = (provisioned_rcu * hours_per_week * self.PROVISIONED_RCU_HOUR +
                                    provisioned_wcu * hours_per_week * self.PROVISIONED_WCU_HOUR)
        monthly_provisioned_cost = weekly_provisioned_cost * 4.33

        # Calculate savings
        savings = monthly_on_demand_cost - monthly_provisioned_cost
        savings_pct = (savings / monthly_on_demand_cost * 100) if monthly_on_demand_cost > 0 else 0

        # Only report if savings > 20%
        if savings_pct < 20 or savings < 5:
            return

        finding = Finding(
            pattern_id=self.PATTERN_ID,
            resource_id=table_name,
            resource_type="DynamoDB Table",
            region=region,
            monthly_impact_usd=savings,
            summary=f"Switch to Provisioned capacity (RCU: {provisioned_rcu}, WCU: {provisioned_wcu}). "
                          f"Traffic is steady (CV: {cv_rcu:.2f}/{cv_wcu:.2f}). Save ~{savings_pct:.0f}%",
            risk_tier=RiskTier.MEDIUM,
            safe_to_fix=False,  # Capacity mode changes need careful planning
            fix_command=f"aws dynamodb update-table --table-name {table_name} "
                       f"--billing-mode PROVISIONED "
                       f"--provisioned-throughput ReadCapacityUnits={provisioned_rcu},WriteCapacityUnits={provisioned_wcu} "
                       f"--region {region}",
            metadata={
                "current_mode": "ON_DEMAND",
                "recommended_mode": "PROVISIONED",
                "avg_rcu": round(avg_rcu, 2),
                "avg_wcu": round(avg_wcu, 2),
                "recommended_rcu": provisioned_rcu,
                "recommended_wcu": provisioned_wcu,
                "cv_rcu": round(cv_rcu, 3),
                "cv_wcu": round(cv_wcu, 3),
                "monthly_on_demand_cost": round(monthly_on_demand_cost, 2),
                "monthly_provisioned_cost": round(monthly_provisioned_cost, 2),
                "savings_pct": round(savings_pct, 1),
            }
        )
        self._findings.append(finding)

    def _check_provisioned_table(self, cloudwatch, table, table_name: str, region: str,
                                  start_time: datetime, end_time: datetime):
        """Check if Provisioned table is underutilized."""
        # Get provisioned capacity
        provisioned_throughput = table.get('ProvisionedThroughput', {})
        provisioned_rcu = provisioned_throughput.get('ReadCapacityUnits', 0)
        provisioned_wcu = provisioned_throughput.get('WriteCapacityUnits', 0)

        if provisioned_rcu == 0 and provisioned_wcu == 0:
            return  # No provisioned capacity (probably on-demand with outdated billing mode)

        # Get consumed capacity metrics
        consumed_read = self._get_metric_stats(
            cloudwatch, table_name, 'ConsumedReadCapacityUnits', start_time, end_time
        )
        consumed_write = self._get_metric_stats(
            cloudwatch, table_name, 'ConsumedWriteCapacityUnits', start_time, end_time
        )

        avg_rcu = consumed_read.get('average', 0)
        avg_wcu = consumed_write.get('average', 0)

        # Calculate utilization
        rcu_util = avg_rcu / provisioned_rcu if provisioned_rcu > 0 else 0
        wcu_util = avg_wcu / provisioned_wcu if provisioned_wcu > 0 else 0

        # Check for underutilization
        if rcu_util >= self.UTILIZATION_THRESHOLD and wcu_util >= self.UTILIZATION_THRESHOLD:
            return  # Well utilized

        # Calculate current cost
        hours_per_month = 730
        monthly_provisioned_cost = (provisioned_rcu * hours_per_month * self.PROVISIONED_RCU_HOUR +
                                     provisioned_wcu * hours_per_month * self.PROVISIONED_WCU_HOUR)

        # Calculate optimized cost (scale down to actual usage + 20% buffer)
        optimal_rcu = max(1, int(avg_rcu * 1.2))
        optimal_wcu = max(1, int(avg_wcu * 1.2))
        optimal_cost = (optimal_rcu * hours_per_month * self.PROVISIONED_RCU_HOUR +
                        optimal_wcu * hours_per_month * self.PROVISIONED_WCU_HOUR)

        savings = monthly_provisioned_cost - optimal_cost

        if savings < 5:
            return  # Not worth the change

        # Determine risk_tier based on utilization
        avg_util = (rcu_util + wcu_util) / 2
        if avg_util < 0.05:
            risk_tier= RiskTier.HIGH
        elif avg_util < 0.10:
            risk_tier= RiskTier.MEDIUM
        else:
            risk_tier= RiskTier.LOW

        finding = Finding(
            pattern_id=self.PATTERN_ID,
            resource_id=table_name,
            resource_type="DynamoDB Table",
            region=region,
            monthly_impact_usd=savings,
            summary=f"Reduce provisioned capacity (RCU: {provisioned_rcu}→{optimal_rcu}, "
                          f"WCU: {provisioned_wcu}→{optimal_wcu}). "
                          f"Utilization: {rcu_util*100:.1f}%/{wcu_util*100:.1f}%",
            risk_tier=risk_tier,
            safe_to_fix=False,  # Capacity changes need testing
            fix_command=f"aws dynamodb update-table --table-name {table_name} "
                       f"--provisioned-throughput ReadCapacityUnits={optimal_rcu},WriteCapacityUnits={optimal_wcu} "
                       f"--region {region}",
            metadata={
                "current_mode": "PROVISIONED",
                "provisioned_rcu": provisioned_rcu,
                "provisioned_wcu": provisioned_wcu,
                "avg_consumed_rcu": round(avg_rcu, 2),
                "avg_consumed_wcu": round(avg_wcu, 2),
                "rcu_utilization": round(rcu_util * 100, 1),
                "wcu_utilization": round(wcu_util * 100, 1),
                "optimal_rcu": optimal_rcu,
                "optimal_wcu": optimal_wcu,
                "current_monthly_cost": round(monthly_provisioned_cost, 2),
                "optimal_monthly_cost": round(optimal_cost, 2),
            }
        )
        self._findings.append(finding)

    def _get_metric_stats(self, cloudwatch, table_name: str, metric_name: str,
                          start_time: datetime, end_time: datetime) -> dict:
        """Get CloudWatch metric statistics."""
        try:
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/DynamoDB',
                MetricName=metric_name,
                Dimensions=[{'Name': 'TableName', 'Value': table_name}],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,  # 1 hour
                Statistics=['Sum', 'Average', 'Maximum']
            )

            datapoints = response.get('Datapoints', [])
            if not datapoints:
                return {'datapoints': [], 'average': 0, 'sum': 0, 'cv': 1.0}

            averages = [dp['Average'] for dp in datapoints]
            total = sum(dp['Sum'] for dp in datapoints)
            avg = sum(averages) / len(averages)

            # Calculate coefficient of variation
            if avg > 0:
                variance = sum((x - avg) ** 2 for x in averages) / len(averages)
                std_dev = variance ** 0.5
                cv = std_dev / avg
            else:
                cv = 1.0

            return {
                'datapoints': datapoints,
                'average': avg,
                'sum': total,
                'cv': cv
            }
        except Exception:
            return {'datapoints': [], 'average': 0, 'sum': 0, 'cv': 1.0}
