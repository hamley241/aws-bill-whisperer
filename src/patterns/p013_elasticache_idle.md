# p013_elasticache_idle

**Pattern ID:** p013  
**Name:** Idle ElastiCache Clusters  
**Severity:** Medium-High

## What It Detects

ElastiCache clusters (Redis and Memcached) with zero or near-zero connections over 7 days:
1. **Redis replication groups** with no client connections
2. **Memcached clusters** with no client connections

| Instance Type | Hourly Cost | Monthly (24/7) |
|--------------|-------------|----------------|
| cache.t3.micro | ~$0.017/hr | ~$12/mo |
| cache.t4g.medium | ~$0.065/hr | ~$47/mo |
| cache.r6g.large | ~$0.162/hr | ~$117/mo |
| cache.r6g.xlarge | ~$0.324/hr | ~$233/mo |
| cache.r6g.4xlarge | ~$1.297/hr | ~$934/mo |

**Note:** Multi-AZ configurations effectively double the cost.

## Why It Matters

- **24/7 billing:** ElastiCache charges hourly, even with zero traffic
- **Hidden clusters:** Easy to forget dev/test caches
- **Multi-AZ waste:** HA configurations double idle waste
- **Memory costs:** Memory-optimized instances (R-series) are expensive

## Cost Estimation

```python
# Monthly cost calculation
hourly_cost = 0.162  # cache.r6g.large
node_count = 2        # Primary + replica
monthly_cost = hourly_cost * 24 * 30 * node_count
# = $0.162 * 720 * 2 = $233.28/month

# Multi-AZ Redis cluster
nodes_per_shard = 2   # Primary + replica
num_shards = 3
total_nodes = nodes_per_shard * num_shards  # 6 nodes
monthly_cost = 0.162 * 720 * 6  # = $699.84/month
```

## Agent Actions

### CLI Usage

```bash
# Scan for idle ElastiCache clusters
python whisper.py scan --pattern 013 --json

# Show human-readable
python whisper.py scan --pattern 013

# Scan specific region
python whisper.py scan --pattern 013 --regions us-east-1 eu-west-1
```

### Python Usage

```python
from whisper import get_pattern_by_id

pattern = get_pattern_by_id("013")
findings = pattern().scan()

for f in findings:
    print(f"{f.resource_type}: {f.resource_id}")
    print(f"  Engine: {f.metadata.get('engine')}")
    print(f"  Nodes: {f.metadata.get('node_count')} x {f.metadata.get('instance_type')}")
    print(f"  Connections (7d): {f.metadata.get('connections_7d')}")
    print(f"  Monthly cost: ${f.monthly_cost:.2f}")
```

## Fix Workflow

### For Redis Clusters

1. **Verify no connections** → Check CloudWatch CurrConnections metric
2. **Check application configs** → Any apps configured to use this cluster?
3. **Create snapshot** (optional) → Preserve data if needed
4. **Delete replication group** → Removes all nodes

```bash
# Create final snapshot before deletion
aws elasticache create-snapshot \
    --replication-group-id my-redis-cluster \
    --snapshot-name my-redis-final-snapshot

# Delete Redis replication group
aws elasticache delete-replication-group \
    --replication-group-id my-redis-cluster \
    --final-snapshot-identifier my-redis-final \
    --region us-east-1
```

### For Memcached Clusters

1. **Verify no connections** → Check CloudWatch metrics
2. **Delete cluster** → Memcached has no persistence, just delete

```bash
# Delete Memcached cluster
aws elasticache delete-cache-cluster \
    --cache-cluster-id my-memcached-cluster \
    --region us-east-1
```

### Safety Rules

- ⚠️ **Manual review required:** All findings require human judgment
- ✅ **Redis:** Can create final snapshot before deletion
- ❌ **Memcached:** No snapshot support (ephemeral by design)
- ⚠️ **Check apps:** Verify no applications expect this cache

## Integration Points

### For Agents (Rusty, etc.)

```python
# In agent code
from whisper import get_pattern_by_id

def check_elasticache_waste():
    pattern = get_pattern_by_id("013")
    findings = pattern().scan()
    
    if findings:
        total_waste = sum(f.monthly_cost for f in findings)
        redis = [f for f in findings if f.metadata.get('engine') == 'redis']
        memcached = [f for f in findings if f.metadata.get('engine') == 'memcached']
        
        return f"Found {len(redis)} idle Redis and {len(memcached)} idle Memcached clusters. " \
               f"${total_waste:.2f}/mo waste."
    return "No idle ElastiCache clusters found"
```

### Cron Job

```yaml
# Run weekly
schedule: "0 9 * * 1"
command: python whisper.py scan --pattern 013 --json > findings.json
```

## Output Format

```json
{
  "pattern_id": "013",
  "findings": [
    {
      "resource_id": "session-cache-prod",
      "resource_type": "ElastiCache Redis Cluster",
      "region": "us-east-1",
      "monthly_cost": 233.28,
      "severity": "high",
      "safe_to_fix": false,
      "recommendation": "Redis cluster has 0 connections in 7 days. Consider deleting. Nodes: 2 x cache.r6g.large",
      "metadata": {
        "engine": "redis",
        "status": "available",
        "instance_type": "cache.r6g.large",
        "node_count": 2,
        "connections_7d": 0,
        "avg_connections_per_day": 0.0,
        "multi_az": true,
        "automatic_failover": "enabled",
        "hourly_cost_per_node": 0.162
      }
    }
  ]
}
```

## Common Patterns to Watch

| Pattern | Likely Action |
|---------|--------------|
| `-dev-` or `-test-` in name | Delete after testing |
| Multi-AZ with 0 traffic | Expensive HA for nothing |
| R-series instances | Memory-optimized = costly |
| `NumCacheNodes > 3` | Sharded cluster, high cost |

## Prevention Tips

1. **Tag resources:** Add `owner`, `environment`, and `expiry` tags
2. **Use parameter groups:** Centralize configuration
3. **Set CloudWatch alarms:** Alert on zero connections
4. **Review periodically:** Schedule monthly cache audits
5. **Consider serverless:** ElastiCache Serverless for variable workloads
