"""
Pattern 018: Idle Redshift and EMR Clusters
Detects Redshift and EMR clusters that are idle (no queries/jobs for extended periods).
These are very expensive resources that should be terminated when not in use.
"""

import logging
from datetime import datetime, timedelta, timezone

from .base import BasePattern, Complexity, Finding, RemediationMode, RemediationResult, RiskTier, Category


logger = logging.getLogger(__name__)


class RedshiftEMRIdlePattern(BasePattern):
    PATTERN_ID = "018"
    NAME = "Idle Redshift and EMR Clusters"
    DESCRIPTION = "Redshift and EMR clusters idle after use - very high cost waste"
    COMPLEXITY = Complexity.MEDIUM
    SERVICES = ["redshift", "emr", "cloudwatch"]
    CATEGORY = Category.DATABASE
    REQUIRED_IAM = ["redshift:DescribeClusters", "elasticmapreduce:ListClusters", "cloudwatch:GetMetricStatistics", "ec2:DescribeRegions"]

    # Pricing (approximate, varies by instance type and region)
    # Redshift: dc2.large ~$0.25/hour, ra3.xlplus ~$1.086/hour
    # EMR: m5.xlarge ~$0.10/hour (EMR fee) + EC2 cost
    REDSHIFT_BASE_HOURLY = 0.25  # dc2.large baseline
    EMR_CORE_HOURLY = 0.10  # EMR fee per node-hour (varies by instance)

    # Instance type pricing multipliers (relative to base)
    REDSHIFT_INSTANCE_MULTIPLIER = {
        'dc2.large': 1.0,
        'dc2.8xlarge': 19.04,
        'ra3.xlplus': 4.34,
        'ra3.4xlarge': 13.04,
        'ra3.16xlarge': 52.16,
    }

    EMR_INSTANCE_MULTIPLIER = {
        'm5.xlarge': 1.0,
        'm5.2xlarge': 2.0,
        'm5.4xlarge': 4.0,
        'm5.8xlarge': 8.0,
        'r5.xlarge': 1.26,
        'r5.2xlarge': 2.52,
    }

    # Thresholds
    IDLE_HOURS_THRESHOLD = 24  # Consider idle after 24 hours of no activity

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                self._scan_redshift(region)
            except Exception:
                logger.exception("p018 error scanning Redshift in region %s", region)

            try:
                self._scan_emr(region)
            except Exception:
                logger.exception("p018 error scanning EMR in region %s", region)

        return self._findings

    def _scan_redshift(self, region: str):
        """Scan for idle Redshift clusters."""
        redshift = self.session.client('redshift', region_name=region)
        cloudwatch = self.session.client('cloudwatch', region_name=region)

        try:
            paginator = redshift.get_paginator('describe_clusters')
            for page in paginator.paginate():
                for cluster in page.get('Clusters', []):
                    self._analyze_redshift_cluster(cluster, cloudwatch, region)
        except Exception:
            logger.exception("p018 error listing Redshift clusters in region %s", region)

    def _analyze_redshift_cluster(self, cluster: dict, cloudwatch, region: str):
        """Analyze a single Redshift cluster for idle status."""
        cluster_id = cluster['ClusterIdentifier']
        status = cluster.get('ClusterStatus', 'unknown')

        # Only check available clusters
        if status != 'available':
            return

        node_type = cluster.get('NodeType', 'dc2.large')
        num_nodes = cluster.get('NumberOfNodes', 1)

        # Get query activity from CloudWatch
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=self.IDLE_HOURS_THRESHOLD)

        # Check DatabaseConnections metric
        connections = self._get_redshift_metric(
            cloudwatch, cluster_id, 'DatabaseConnections', start_time, end_time
        )

        # Check CPUUtilization
        cpu_util = self._get_redshift_metric(
            cloudwatch, cluster_id, 'CPUUtilization', start_time, end_time
        )

        # Determine if idle: no connections and very low CPU
        max_connections = connections.get('max', 0)
        avg_cpu = cpu_util.get('average', 0)

        is_idle = max_connections < 1 and avg_cpu < 5

        if not is_idle:
            return

        # Calculate cost
        multiplier = self.REDSHIFT_INSTANCE_MULTIPLIER.get(node_type, 1.0)
        hourly_cost = self.REDSHIFT_BASE_HOURLY * multiplier * num_nodes
        monthly_impact_usd= hourly_cost * 730  # hours per month

        # Get cluster age and last modified time
        create_time = cluster.get('ClusterCreateTime')
        cluster_age_days = (datetime.now(timezone.utc) - create_time).days if create_time else 0

        finding = Finding(
            pattern_id=self.PATTERN_ID,
            resource_id=cluster_id,
            resource_type="Redshift Cluster",
            region=region,
            monthly_impact_usd=monthly_impact_usd,
            summary=f"Idle Redshift cluster ({node_type} x{num_nodes}). "
                          f"No connections for {self.IDLE_HOURS_THRESHOLD}h. "
                          f"Consider pausing, snapshotting, or deleting.",
            risk_tier=RiskTier.HIGH,
            safe_to_fix=False,  # Cluster deletion is destructive
            fix_command=f"aws redshift pause-cluster --cluster-identifier {cluster_id} --region {region}",
            metadata={
                "service": "redshift",
                "node_type": node_type,
                "num_nodes": num_nodes,
                "status": status,
                "max_connections_24h": max_connections,
                "avg_cpu_24h": round(avg_cpu, 2),
                "hourly_cost": round(hourly_cost, 2),
                "cluster_age_days": cluster_age_days,
                "create_time": create_time.isoformat() if create_time else None,
            }
        )
        self._findings.append(finding)

    def _get_redshift_metric(self, cloudwatch, cluster_id: str, metric_name: str,
                              start_time: datetime, end_time: datetime) -> dict:
        """Get CloudWatch metric for Redshift cluster."""
        try:
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/Redshift',
                MetricName=metric_name,
                Dimensions=[{'Name': 'ClusterIdentifier', 'Value': cluster_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=['Average', 'Maximum', 'Sum']
            )

            datapoints = response.get('Datapoints', [])
            if not datapoints:
                return {'average': 0, 'max': 0, 'sum': 0}

            return {
                'average': sum(dp['Average'] for dp in datapoints) / len(datapoints),
                'max': max(dp['Maximum'] for dp in datapoints),
                'sum': sum(dp.get('Sum', 0) for dp in datapoints)
            }
        except Exception:
            return {'average': 0, 'max': 0, 'sum': 0}

    def _scan_emr(self, region: str):
        """Scan for idle EMR clusters."""
        emr = self.session.client('emr', region_name=region)
        cloudwatch = self.session.client('cloudwatch', region_name=region)

        try:
            # List clusters in running/waiting states
            paginator = emr.get_paginator('list_clusters')
            for page in paginator.paginate(ClusterStates=['RUNNING', 'WAITING']):
                for cluster_summary in page.get('Clusters', []):
                    cluster_id = cluster_summary['Id']
                    self._analyze_emr_cluster(emr, cloudwatch, cluster_id, region)
        except Exception:
            logger.exception("p018 error listing EMR clusters in region %s", region)

    def _analyze_emr_cluster(self, emr, cloudwatch, cluster_id: str, region: str):
        """Analyze a single EMR cluster for idle status."""
        try:
            # Get cluster details
            cluster = emr.describe_cluster(ClusterId=cluster_id)['Cluster']
            status_state = cluster.get('Status', {}).get('State', 'unknown')

            # Check if cluster is in WAITING state (idle, waiting for work)
            is_waiting = status_state == 'WAITING'

            # Get cluster age
            timeline = cluster.get('Status', {}).get('Timeline', {})
            creation_time = timeline.get('CreationDateTime')
            ready_time = timeline.get('ReadyDateTime')

            if ready_time:
                hours_since_ready = (datetime.now(timezone.utc) - ready_time).total_seconds() / 3600
            else:
                hours_since_ready = 0

            # Get instance groups for cost calculation
            instance_groups = emr.list_instance_groups(ClusterId=cluster_id).get('InstanceGroups', [])
            total_core_nodes = 0
            instance_type = 'm5.xlarge'

            for ig in instance_groups:
                if ig.get('InstanceGroupType') == 'CORE':
                    total_core_nodes += ig.get('RunningInstanceCount', 0)
                    instance_type = ig.get('InstanceType', 'm5.xlarge')

            # Check for job activity
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(hours=self.IDLE_HOURS_THRESHOLD)

            # Check AppsRunning metric
            apps_running = self._get_emr_metric(
                cloudwatch, cluster_id, 'AppsRunning', start_time, end_time
            )

            # Check ContainerAllocated metric
            containers = self._get_emr_metric(
                cloudwatch, cluster_id, 'ContainerAllocated', start_time, end_time
            )

            # Determine if idle
            max_apps = apps_running.get('max', 0)
            max_containers = containers.get('max', 0)

            is_idle = (is_waiting or (max_apps < 1 and max_containers < 2)) and hours_since_ready > self.IDLE_HOURS_THRESHOLD

            if not is_idle:
                return

            # Calculate cost
            multiplier = self.EMR_INSTANCE_MULTIPLIER.get(instance_type, 1.0)
            hourly_cost = self.EMR_CORE_HOURLY * multiplier * max(1, total_core_nodes)
            # Add EC2 cost estimate (roughly 3x EMR fee for m5 instances)
            hourly_cost *= 3
            monthly_impact_usd= hourly_cost * 730

            finding = Finding(
                pattern_id=self.PATTERN_ID,
                resource_id=cluster_id,
                resource_type="EMR Cluster",
                region=region,
                monthly_impact_usd=monthly_impact_usd,
                summary=f"Idle EMR cluster in {status_state} state. "
                              f"No jobs for {self.IDLE_HOURS_THRESHOLD}h. "
                              f"Core nodes: {total_core_nodes}. Consider terminating.",
                risk_tier=RiskTier.HIGH,
                safe_to_fix=False,  # EMR termination is destructive
                fix_command=f"aws emr terminate-clusters --cluster-ids {cluster_id} --region {region}",
                metadata={
                    "service": "emr",
                    "status": status_state,
                    "instance_type": instance_type,
                    "core_nodes": total_core_nodes,
                    "max_apps_24h": max_apps,
                    "max_containers_24h": max_containers,
                    "hours_since_ready": round(hours_since_ready, 1),
                    "hourly_cost": round(hourly_cost, 2),
                    "cluster_name": cluster.get('Name', 'unknown'),
                }
            )
            self._findings.append(finding)

        except Exception:
            logger.exception("p018 error analyzing EMR cluster %s", cluster_id)

    def _get_emr_metric(self, cloudwatch, cluster_id: str, metric_name: str,
                         start_time: datetime, end_time: datetime) -> dict:
        """Get CloudWatch metric for EMR cluster."""
        try:
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/ElasticMapReduce',
                MetricName=metric_name,
                Dimensions=[{'Name': 'JobFlowId', 'Value': cluster_id}],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600,
                Statistics=['Average', 'Maximum', 'Sum']
            )

            datapoints = response.get('Datapoints', [])
            if not datapoints:
                return {'average': 0, 'max': 0, 'sum': 0}

            return {
                'average': sum(dp['Average'] for dp in datapoints) / len(datapoints),
                'max': max(dp['Maximum'] for dp in datapoints),
                'sum': sum(dp.get('Sum', 0) for dp in datapoints)
            }
        except Exception:
            return {'average': 0, 'max': 0, 'sum': 0}
