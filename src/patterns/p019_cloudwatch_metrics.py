"""
Pattern 019: High-Cardinality CloudWatch Custom Metrics
Detects custom metrics with high cardinality dimensions that cause cost explosions.
Each unique dimension combination is a separate metric, billed at $0.30/metric/month.
"""

from datetime import datetime, timedelta, timezone
from collections import defaultdict

from .base import BasePattern, Complexity, Finding, Severity


class CloudWatchMetricsPattern(BasePattern):
    PATTERN_ID = "019"
    NAME = "High-Cardinality CloudWatch Custom Metrics"
    DESCRIPTION = "CloudWatch custom metrics with dimension explosions causing high costs"
    COMPLEXITY = Complexity.MEDIUM
    SERVICES = ["cloudwatch"]

    # Pricing
    METRIC_PRICE_PER_MONTH = 0.30  # First 10,000 metrics
    METRIC_PRICE_TIER_2 = 0.10    # 10,001 - 240,000
    METRIC_PRICE_TIER_3 = 0.05    # 240,001 - 750,000
    DATAPOINT_PRICE = 0.01 / 1000  # $0.01 per 1,000 custom metric datapoints (PutMetricData)

    # Thresholds
    HIGH_CARDINALITY_THRESHOLD = 100  # >100 unique metric streams in a namespace = high cardinality
    DIMENSION_WARNING_THRESHOLD = 5   # >5 dimensions per metric = risky
    MIN_COST_TO_REPORT = 10.0         # Minimum monthly cost to report

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                cloudwatch = self.session.client('cloudwatch', region_name=region)
                self._analyze_custom_metrics(cloudwatch, region)
            except Exception as e:
                print(f"Error scanning CloudWatch in {region}: {e}")
                continue

        return self._findings

    def _analyze_custom_metrics(self, cloudwatch, region: str):
        """Analyze custom metric namespaces for high cardinality."""
        try:
            # List all metric namespaces
            namespaces = self._list_namespaces(cloudwatch)

            # Filter to custom namespaces (exclude AWS/ prefixed ones)
            custom_namespaces = [ns for ns in namespaces if not ns.startswith('AWS/')]

            for namespace in custom_namespaces:
                self._analyze_namespace(cloudwatch, namespace, region)

        except Exception as e:
            print(f"Error analyzing CloudWatch metrics in {region}: {e}")

    def _list_namespaces(self, cloudwatch) -> list[str]:
        """List all metric namespaces."""
        namespaces = set()
        try:
            paginator = cloudwatch.get_paginator('list_metrics')
            # Just get a sample to find namespaces
            for page in paginator.paginate(PaginationConfig={'MaxItems': 1000}):
                for metric in page.get('Metrics', []):
                    namespaces.add(metric['Namespace'])
        except Exception as e:
            print(f"Error listing namespaces: {e}")
        return list(namespaces)

    def _analyze_namespace(self, cloudwatch, namespace: str, region: str):
        """Analyze a single namespace for high cardinality metrics."""
        try:
            # Count metrics and analyze dimensions
            metrics_data = defaultdict(lambda: {
                'count': 0,
                'dimension_sets': set(),
                'max_dimensions': 0,
                'dimension_names': set()
            })

            paginator = cloudwatch.get_paginator('list_metrics')
            total_metric_count = 0

            for page in paginator.paginate(Namespace=namespace):
                for metric in page.get('Metrics', []):
                    metric_name = metric['MetricName']
                    dimensions = metric.get('Dimensions', [])

                    total_metric_count += 1
                    metrics_data[metric_name]['count'] += 1

                    # Track dimension combinations
                    dim_key = tuple(sorted((d['Name'], d['Value']) for d in dimensions))
                    metrics_data[metric_name]['dimension_sets'].add(dim_key)

                    # Track max dimensions
                    num_dims = len(dimensions)
                    metrics_data[metric_name]['max_dimensions'] = max(
                        metrics_data[metric_name]['max_dimensions'], num_dims
                    )

                    # Track dimension names
                    for dim in dimensions:
                        metrics_data[metric_name]['dimension_names'].add(dim['Name'])

            # Check for high cardinality
            if total_metric_count < self.HIGH_CARDINALITY_THRESHOLD:
                return  # Not enough metrics to be a problem

            # Calculate cost
            monthly_cost = self._calculate_metric_cost(total_metric_count)

            if monthly_cost < self.MIN_COST_TO_REPORT:
                return

            # Find the worst offenders (metrics with most unique dimension combinations)
            worst_metrics = sorted(
                metrics_data.items(),
                key=lambda x: x[1]['count'],
                reverse=True
            )[:5]

            # Determine severity based on cardinality and cost
            if total_metric_count > 10000 or monthly_cost > 1000:
                severity = Severity.HIGH
            elif total_metric_count > 1000 or monthly_cost > 100:
                severity = Severity.MEDIUM
            else:
                severity = Severity.LOW

            # Build recommendation
            high_dim_metrics = [
                name for name, data in metrics_data.items()
                if data['max_dimensions'] > self.DIMENSION_WARNING_THRESHOLD
            ]

            recommendation = (
                f"Namespace '{namespace}' has {total_metric_count} unique metric streams. "
                f"Top metrics: {', '.join(m[0] for m in worst_metrics[:3])}. "
            )

            if high_dim_metrics:
                recommendation += f"High-dimension metrics ({len(high_dim_metrics)}): consider reducing dimensions."
            else:
                recommendation += "Consider using metric math or reducing dimension cardinality."

            finding = Finding(
                resource_id=namespace,
                resource_type="CloudWatch Custom Namespace",
                region=region,
                monthly_cost=monthly_cost,
                recommendation=recommendation,
                severity=severity,
                safe_to_fix=False,  # Requires application changes
                fix_command=None,  # No simple fix command
                metadata={
                    "namespace": namespace,
                    "total_metric_streams": total_metric_count,
                    "unique_metric_names": len(metrics_data),
                    "top_metrics": [
                        {
                            "name": name,
                            "stream_count": data['count'],
                            "max_dimensions": data['max_dimensions'],
                            "dimension_names": list(data['dimension_names'])[:10]
                        }
                        for name, data in worst_metrics
                    ],
                    "high_dimension_metrics": high_dim_metrics[:10],
                    "estimated_put_cost": round(self._estimate_put_cost(total_metric_count), 2),
                }
            )
            self._findings.append(finding)

        except Exception as e:
            print(f"Error analyzing namespace {namespace}: {e}")

    def _calculate_metric_cost(self, metric_count: int) -> float:
        """Calculate monthly cost for a number of custom metrics."""
        cost = 0.0

        if metric_count <= 10000:
            cost = metric_count * self.METRIC_PRICE_PER_MONTH
        elif metric_count <= 240000:
            cost = (10000 * self.METRIC_PRICE_PER_MONTH +
                   (metric_count - 10000) * self.METRIC_PRICE_TIER_2)
        elif metric_count <= 750000:
            cost = (10000 * self.METRIC_PRICE_PER_MONTH +
                   230000 * self.METRIC_PRICE_TIER_2 +
                   (metric_count - 240000) * self.METRIC_PRICE_TIER_3)
        else:
            cost = (10000 * self.METRIC_PRICE_PER_MONTH +
                   230000 * self.METRIC_PRICE_TIER_2 +
                   510000 * self.METRIC_PRICE_TIER_3 +
                   (metric_count - 750000) * self.METRIC_PRICE_TIER_3)

        return cost

    def _estimate_put_cost(self, metric_count: int) -> float:
        """Estimate PutMetricData cost assuming 1 datapoint per metric per minute."""
        # Assume each metric stream gets 1 datapoint per minute
        datapoints_per_month = metric_count * 60 * 24 * 30
        return datapoints_per_month * self.DATAPOINT_PRICE
