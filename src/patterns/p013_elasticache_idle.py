"""
Pattern 013: Idle ElastiCache Clusters
Detects ElastiCache clusters (Redis/Memcached) with zero connections.

ElastiCache costs:
- cache.t3.micro: ~$0.017/hour (~$12/month)
- cache.r6g.large: ~$0.18/hour (~$130/month)
- cache.r6g.xlarge: ~$0.36/hour (~$260/month)
- Multi-AZ doubles the cost

Common waste patterns:
- Dev/test clusters left running
- Clusters for deprecated applications
- Over-provisioned clusters with no traffic
"""
from datetime import datetime, timedelta, timezone

from .base import BasePattern, Complexity, Finding, Severity


class ElastiCacheIdlePattern(BasePattern):
    PATTERN_ID = "013"
    NAME = "Idle ElastiCache Clusters"
    DESCRIPTION = "ElastiCache clusters with zero connections (paying for unused cache)"
    COMPLEXITY = Complexity.MEDIUM
    SERVICES = ["elasticache", "cloudwatch"]

    # Lookback period for connection metrics
    LOOKBACK_DAYS = 7
    
    # Minimum connections threshold (per day average)
    MIN_CONNECTIONS_PER_DAY = 1
    
    # Hourly pricing estimates (approximate, varies by region)
    # Format: instance_type -> hourly cost
    HOURLY_COSTS = {
        # T3 (burstable)
        "cache.t3.micro": 0.017,
        "cache.t3.small": 0.034,
        "cache.t3.medium": 0.068,
        # T4g (Graviton)
        "cache.t4g.micro": 0.016,
        "cache.t4g.small": 0.032,
        "cache.t4g.medium": 0.065,
        # M5 (general purpose)
        "cache.m5.large": 0.142,
        "cache.m5.xlarge": 0.284,
        "cache.m5.2xlarge": 0.569,
        "cache.m5.4xlarge": 1.137,
        # M6g (Graviton)
        "cache.m6g.large": 0.128,
        "cache.m6g.xlarge": 0.256,
        "cache.m6g.2xlarge": 0.512,
        "cache.m6g.4xlarge": 1.023,
        # R5 (memory optimized)
        "cache.r5.large": 0.180,
        "cache.r5.xlarge": 0.361,
        "cache.r5.2xlarge": 0.721,
        "cache.r5.4xlarge": 1.442,
        # R6g (Graviton memory optimized)
        "cache.r6g.large": 0.162,
        "cache.r6g.xlarge": 0.324,
        "cache.r6g.2xlarge": 0.649,
        "cache.r6g.4xlarge": 1.297,
        "cache.r6g.8xlarge": 2.594,
        # R7g (latest Graviton)
        "cache.r7g.large": 0.170,
        "cache.r7g.xlarge": 0.340,
        "cache.r7g.2xlarge": 0.681,
    }

    def scan(self, regions: list[str] = None) -> list[Finding]:
        regions = regions or self.get_all_regions()
        self._findings = []

        for region in regions:
            try:
                elasticache = self.session.client("elasticache", region_name=region)
                cloudwatch = self.session.client("cloudwatch", region_name=region)

                # Scan Redis clusters
                self._scan_redis_clusters(elasticache, cloudwatch, region)
                
                # Scan Memcached clusters
                self._scan_memcached_clusters(elasticache, cloudwatch, region)

            except Exception as e:
                print(f"Error scanning {region}: {e}")
                continue

        return self._findings

    def _scan_redis_clusters(self, elasticache, cloudwatch, region: str):
        """Scan Redis replication groups for idle clusters."""
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=self.LOOKBACK_DAYS)
        
        try:
            paginator = elasticache.get_paginator("describe_replication_groups")
            
            for page in paginator.paginate():
                for repl_group in page.get("ReplicationGroups", []):
                    repl_group_id = repl_group["ReplicationGroupId"]
                    status = repl_group.get("Status", "")
                    
                    # Only check available clusters
                    if status != "available":
                        continue
                    
                    # Get node groups and member clusters
                    node_groups = repl_group.get("NodeGroups", [])
                    member_clusters = repl_group.get("MemberClusters", [])
                    
                    # Get cluster details for instance type
                    instance_type = self._get_redis_instance_type(elasticache, member_clusters)
                    
                    # Count nodes
                    node_count = len(member_clusters) if member_clusters else len(node_groups)
                    if node_count == 0:
                        node_count = 1
                    
                    # Get connection metrics
                    total_connections = self._get_connections(
                        cloudwatch, repl_group_id, "Redis", start_time, end_time
                    )
                    
                    daily_avg = total_connections / self.LOOKBACK_DAYS
                    
                    if daily_avg < self.MIN_CONNECTIONS_PER_DAY:
                        # Calculate monthly cost
                        hourly_cost = self.HOURLY_COSTS.get(instance_type, 0.20)
                        monthly_cost = hourly_cost * 24 * 30 * node_count
                        
                        # Determine severity based on cost
                        if monthly_cost > 300:
                            severity = Severity.CRITICAL
                        elif monthly_cost > 100:
                            severity = Severity.HIGH
                        else:
                            severity = Severity.MEDIUM
                        
                        multi_az = repl_group.get("MultiAZ", "disabled") == "enabled"
                        automatic_failover = repl_group.get("AutomaticFailover", "disabled")
                        
                        finding = Finding(
                            resource_id=repl_group_id,
                            resource_type="ElastiCache Redis Cluster",
                            region=region,
                            monthly_cost=monthly_cost,
                            recommendation=f"Redis cluster has {int(total_connections)} connections in {self.LOOKBACK_DAYS} days "
                                          f"(avg {daily_avg:.1f}/day). Consider deleting if not needed. "
                                          f"Nodes: {node_count} x {instance_type}",
                            severity=severity,
                            safe_to_fix=False,  # Deleting clusters should be manual
                            fix_command=f"aws elasticache delete-replication-group --replication-group-id {repl_group_id} --region {region}",
                            metadata={
                                "engine": "redis",
                                "status": status,
                                "instance_type": instance_type,
                                "node_count": node_count,
                                "connections_7d": int(total_connections),
                                "avg_connections_per_day": round(daily_avg, 2),
                                "multi_az": multi_az,
                                "automatic_failover": automatic_failover,
                                "hourly_cost_per_node": hourly_cost
                            }
                        )
                        self._findings.append(finding)

        except Exception as e:
            print(f"Error scanning Redis clusters in {region}: {e}")

    def _scan_memcached_clusters(self, elasticache, cloudwatch, region: str):
        """Scan Memcached cache clusters for idle clusters."""
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=self.LOOKBACK_DAYS)
        
        try:
            paginator = elasticache.get_paginator("describe_cache_clusters")
            
            for page in paginator.paginate(ShowCacheNodeInfo=True):
                for cluster in page.get("CacheClusters", []):
                    cluster_id = cluster["CacheClusterId"]
                    engine = cluster.get("Engine", "")
                    status = cluster.get("CacheClusterStatus", "")
                    
                    # Only check Memcached clusters that are available
                    # Redis clusters are handled via replication groups
                    if engine != "memcached" or status != "available":
                        continue
                    
                    instance_type = cluster.get("CacheNodeType", "cache.t3.medium")
                    node_count = cluster.get("NumCacheNodes", 1)
                    
                    # Get connection metrics
                    total_connections = self._get_connections(
                        cloudwatch, cluster_id, "Memcached", start_time, end_time
                    )
                    
                    daily_avg = total_connections / self.LOOKBACK_DAYS
                    
                    if daily_avg < self.MIN_CONNECTIONS_PER_DAY:
                        # Calculate monthly cost
                        hourly_cost = self.HOURLY_COSTS.get(instance_type, 0.10)
                        monthly_cost = hourly_cost * 24 * 30 * node_count
                        
                        # Determine severity based on cost
                        if monthly_cost > 200:
                            severity = Severity.HIGH
                        elif monthly_cost > 50:
                            severity = Severity.MEDIUM
                        else:
                            severity = Severity.LOW
                        
                        finding = Finding(
                            resource_id=cluster_id,
                            resource_type="ElastiCache Memcached Cluster",
                            region=region,
                            monthly_cost=monthly_cost,
                            recommendation=f"Memcached cluster has {int(total_connections)} connections in {self.LOOKBACK_DAYS} days "
                                          f"(avg {daily_avg:.1f}/day). Consider deleting if not needed. "
                                          f"Nodes: {node_count} x {instance_type}",
                            severity=severity,
                            safe_to_fix=False,
                            fix_command=f"aws elasticache delete-cache-cluster --cache-cluster-id {cluster_id} --region {region}",
                            metadata={
                                "engine": "memcached",
                                "status": status,
                                "instance_type": instance_type,
                                "node_count": node_count,
                                "connections_7d": int(total_connections),
                                "avg_connections_per_day": round(daily_avg, 2),
                                "hourly_cost_per_node": hourly_cost
                            }
                        )
                        self._findings.append(finding)

        except Exception as e:
            print(f"Error scanning Memcached clusters in {region}: {e}")

    def _get_redis_instance_type(self, elasticache, member_clusters: list) -> str:
        """Get instance type from Redis cluster members."""
        if not member_clusters:
            return "cache.r6g.large"  # Default assumption
        
        try:
            # Get first member cluster details
            response = elasticache.describe_cache_clusters(
                CacheClusterId=member_clusters[0],
                ShowCacheNodeInfo=True
            )
            clusters = response.get("CacheClusters", [])
            if clusters:
                return clusters[0].get("CacheNodeType", "cache.r6g.large")
        except Exception:
            pass
        
        return "cache.r6g.large"

    def _get_connections(self, cloudwatch, cluster_id: str, engine: str, 
                         start_time: datetime, end_time: datetime) -> float:
        """Get total connection count for a cluster."""
        # ElastiCache metric names differ by engine
        if engine == "Redis":
            metric_name = "CurrConnections"
            namespace = "AWS/ElastiCache"
        else:  # Memcached
            metric_name = "CurrConnections"
            namespace = "AWS/ElastiCache"
        
        try:
            response = cloudwatch.get_metric_statistics(
                Namespace=namespace,
                MetricName=metric_name,
                Dimensions=[
                    {"Name": "CacheClusterId", "Value": cluster_id}
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=3600 * 24,  # Daily granularity
                Statistics=["Maximum"],
            )
            
            data_points = response.get("Datapoints", [])
            if not data_points:
                # Try with replication group dimension for Redis
                if engine == "Redis":
                    response = cloudwatch.get_metric_statistics(
                        Namespace=namespace,
                        MetricName=metric_name,
                        Dimensions=[
                            {"Name": "ReplicationGroupId", "Value": cluster_id}
                        ],
                        StartTime=start_time,
                        EndTime=end_time,
                        Period=3600 * 24,
                        Statistics=["Maximum"],
                    )
                    data_points = response.get("Datapoints", [])
            
            # Sum up max connections per day
            total = sum(dp.get("Maximum", 0) for dp in data_points)
            return total
            
        except Exception:
            return 0

    def fix(self, finding: Finding, dry_run: bool = True) -> bool:
        """Apply fix for idle ElastiCache clusters."""
        if not finding.safe_to_fix:
            raise ValueError(
                f"Cannot safely fix {finding.resource_id}. "
                f"Deleting cache clusters requires manual confirmation."
            )
        
        if dry_run:
            print(f"[DRY RUN] Would execute: {finding.fix_command}")
            return True
        
        elasticache = self.session.client("elasticache", region_name=finding.region)
        
        try:
            if "Redis" in finding.resource_type:
                elasticache.delete_replication_group(
                    ReplicationGroupId=finding.resource_id,
                    RetainPrimaryCluster=False
                )
            else:  # Memcached
                elasticache.delete_cache_cluster(
                    CacheClusterId=finding.resource_id
                )
            return True
        except Exception as e:
            print(f"Error deleting {finding.resource_id}: {e}")
            return False
