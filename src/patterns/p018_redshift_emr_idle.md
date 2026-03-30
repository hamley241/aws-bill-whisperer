# p018_redshift_emr_idle

**Pattern ID:** p018  
**Name:** Idle Redshift and EMR Clusters  
**Severity:** High

## What It Detects

Redshift and EMR clusters sitting idle after use:
1. **Idle Redshift clusters** - No database connections, low CPU for 24h+
2. **Idle EMR clusters** - No jobs running, in WAITING state for 24h+

| Metric | Value |
|--------|-------|
| Redshift Cost | $0.25/hour (dc2.large) to $13/hour (ra3.16xlarge) per node |
| EMR Cost | $0.10/hour EMR fee + EC2 cost (~$0.30/hour for m5.xlarge) |
| Idle Threshold | 24 hours of no activity |

## Why It Matters

- **Very High Cost:** Redshift clusters can cost $180-$9,500+/month per node
- **EMR Waste:** Running clusters burn money even with no jobs
- **Common Issue:** Dev/test clusters left running after use
- **Quick Win:** Terminate or pause unused clusters for immediate savings

## Cost Estimation

```python
# Redshift Pricing (per node-hour)
REDSHIFT_HOURLY = {
    'dc2.large': 0.25,      # $182/month
    'dc2.8xlarge': 4.80,    # $3,504/month
    'ra3.xlplus': 1.086,    # $793/month
    'ra3.4xlarge': 3.26,    # $2,380/month
    'ra3.16xlarge': 13.04,  # $9,519/month
}

# EMR Pricing (EMR fee + EC2, per node-hour)
EMR_HOURLY = {
    'm5.xlarge': 0.30,   # $219/month
    'm5.2xlarge': 0.60,  # $438/month
    'r5.xlarge': 0.38,   # $277/month
}
```

## Agent Actions

### CLI Usage

```bash
# Scan for idle Redshift/EMR clusters
python whisper.py scan --pattern 018 --json

# Show human-readable
python whisper.py scan --pattern 018

# Scan specific region
python whisper.py scan --pattern 018 --regions us-east-1
```

### Python Usage

```python
from whisper import get_pattern_by_id

pattern = get_pattern_by_id("018")
findings = pattern().scan()

for f in findings:
    service = f.metadata.get('service')
    print(f"[{service}] {f.resource_id}: ${f.monthly_cost:.2f}/mo")
```

## Fix Workflow

### For Idle Redshift Clusters

1. **Verify idle status** → Check CloudWatch metrics manually
2. **Create snapshot** → Backup before any action
3. **Option A: Pause** → `aws redshift pause-cluster` (preserves data, stops billing)
4. **Option B: Delete** → After snapshot, if truly unused

```bash
# Pause cluster (can resume later)
aws redshift pause-cluster --cluster-identifier my-cluster --region us-east-1

# Create snapshot before deletion
aws redshift create-cluster-snapshot --cluster-identifier my-cluster --snapshot-identifier my-cluster-final

# Delete cluster (DESTRUCTIVE)
aws redshift delete-cluster --cluster-identifier my-cluster --skip-final-cluster-snapshot
```

### For Idle EMR Clusters

1. **Check for pending jobs** → Verify nothing is scheduled
2. **Export logs** → Save any important logs from S3
3. **Terminate** → EMR clusters are ephemeral by design

```bash
# Terminate EMR cluster
aws emr terminate-clusters --cluster-ids j-XXXXXXXXXXXXX --region us-east-1
```

### Safety Rules

- ⚠️ **Never auto-delete** - Requires human verification
- ✅ **Pause Redshift first** - Pause is reversible, delete is not
- ❌ **Don't terminate production EMR** - Check with team first
- ✅ **Create snapshots** - Always before any destructive action

## Integration Points

### For Agents (Rusty, etc.)

```python
from whisper import get_pattern_by_id

def check_idle_clusters():
    pattern = get_pattern_by_id("018")
    findings = pattern().scan()
    
    if findings:
        total_waste = sum(f.monthly_cost for f in findings)
        redshift = [f for f in findings if f.metadata.get('service') == 'redshift']
        emr = [f for f in findings if f.metadata.get('service') == 'emr']
        
        return (f"⚠️ Found {len(redshift)} idle Redshift, {len(emr)} idle EMR clusters. "
                f"Burning ${total_waste:.2f}/mo!")
    return "No idle Redshift/EMR clusters found"
```

### Alert Notification

```python
# High-priority alert for expensive idle resources
if findings:
    for f in findings:
        if f.monthly_cost > 500:
            send_alert(f"🚨 {f.resource_id} idle, wasting ${f.monthly_cost:.0f}/mo!")
```

## Output Format

```json
{
  "pattern_id": "018",
  "findings": [
    {
      "resource_id": "my-redshift-cluster",
      "resource_type": "Redshift Cluster",
      "region": "us-east-1",
      "monthly_cost": 547.50,
      "severity": "high",
      "safe_to_fix": false,
      "recommendation": "Idle Redshift cluster (dc2.large x3). No connections for 24h. Consider pausing, snapshotting, or deleting.",
      "metadata": {
        "service": "redshift",
        "node_type": "dc2.large",
        "num_nodes": 3,
        "max_connections_24h": 0,
        "avg_cpu_24h": 2.1,
        "hourly_cost": 0.75
      }
    },
    {
      "resource_id": "j-XXXXXXXXXXXXX",
      "resource_type": "EMR Cluster",
      "region": "us-east-1",
      "monthly_cost": 657.00,
      "severity": "high",
      "safe_to_fix": false,
      "recommendation": "Idle EMR cluster in WAITING state. No jobs for 24h. Core nodes: 3. Consider terminating.",
      "metadata": {
        "service": "emr",
        "status": "WAITING",
        "instance_type": "m5.xlarge",
        "core_nodes": 3,
        "max_apps_24h": 0
      }
    }
  ]
}
```

## Related Patterns

- **p004_idle_ec2** - Similar idle detection for EC2
- **p007_idle_rds** - Similar idle detection for RDS
