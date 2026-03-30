# p014_ecs_fargate_idle

**Pattern ID:** p014  
**Name:** Idle ECS/Fargate Tasks  
**Severity:** Medium-High

## What It Detects

ECS services with:
- Zero requests over 7 days (if load balancer attached)
- < 1% CPU utilization over 7 days
- Fargate tasks left running after testing/deployments

| Metric | Value |
|--------|-------|
| Monthly Cost | ~$0.04-0.10/vCPU-hour for Fargate |
| Detection | CloudWatch CPUUtilization + ALB RequestCount |
| Lookback | 7 days |

## Why It Matters

- **Waste:** Forgotten Fargate services can cost $30-300+/month per service
- **Risk:** Old deployments, test environments, abandoned microservices
- **Opportunity:** Scale to 0 or delete unused services

## Cost Estimation

```python
# Fargate pricing (us-east-1, on-demand)
VCPU_HOUR = 0.04048  # per vCPU-hour
GB_HOUR = 0.004445   # per GB-hour

# Example: 1 vCPU, 2GB, running 24/7
monthly_cost = (0.04048 * 730) + (2 * 0.004445 * 730)  # ~$36/month
```

## Agent Actions

### CLI Usage

```bash
# Scan for idle ECS services
python whisper.py scan --pattern 014 --json

# Human-readable output
python whisper.py scan --pattern 014

# Filter by region
python whisper.py scan --pattern 014 --region us-east-1

# Preview fix (scale to 0)
python whisper.py fix 014 arn:aws:ecs:us-east-1:123456789:service/cluster/svc --dry-run
```

### Python Usage

```python
from whisper import get_pattern_by_id

pattern = get_pattern_by_id("014")
findings = pattern().scan()

for f in findings:
    print(f"{f.metadata['service_name']}: ${f.monthly_cost:.2f}/mo")
    print(f"  CPU: {f.metadata['avg_cpu_7d']:.2f}%")
    print(f"  Tasks: {f.metadata['running_count']}")
```

## Fix Workflow

1. **Identify** → Find idle services with low CPU/zero requests
2. **Verify** → Check if service is actually unused (logs, dependencies)
3. **Scale Down** → Set desired-count to 0 first (reversible)
4. **Monitor** → Wait for any issues
5. **Delete** → Remove service if truly unused

### Safety Rules

- ✅ **Scale to 0 first** → Always reversible
- ⚠️ **Check dependencies** → Other services may depend on this
- ⚠️ **Check scheduled tasks** → May be a cron-style service
- ❌ **Never auto-delete** → Services can have CloudFormation/Terraform refs

## Integration Points

### For Agents

```python
from whisper import get_pattern_by_id

def check_idle_ecs():
    pattern = get_pattern_by_id("014")
    findings = pattern().scan(regions=["us-east-1", "us-west-2"])
    
    if findings:
        total = sum(f.monthly_cost for f in findings)
        services = [f.metadata['service_name'] for f in findings]
        return f"Found {len(findings)} idle ECS services: {services}. ${total:.2f}/mo waste"
    return "No idle ECS services found"
```

### Cron Job

```yaml
# Run weekly - ECS services don't change often
schedule: "0 10 * * 1"
command: python whisper.py scan --pattern 014 --json > ecs_idle.json
```

## Output Format

```json
{
  "pattern_id": "014",
  "findings": [
    {
      "resource_id": "arn:aws:ecs:us-east-1:123456789:service/my-cluster/api-service",
      "resource_type": "ECS Service",
      "region": "us-east-1",
      "monthly_cost": 72.50,
      "severity": "medium",
      "safe_to_fix": false,
      "fix_command": "aws ecs update-service --cluster my-cluster --service api-service --desired-count 0 --region us-east-1",
      "recommendation": "ECS service 'api-service' appears idle (CPU 0.12%, zero requests) for 7 days.",
      "metadata": {
        "cluster_name": "my-cluster",
        "service_name": "api-service",
        "launch_type": "FARGATE",
        "running_count": 2,
        "avg_cpu_7d": 0.12,
        "request_count_7d": 0,
        "task_cpu": 0.5,
        "task_memory_mb": 1024
      }
    }
  ]
}
```

## Common Scenarios

| Scenario | Action |
|----------|--------|
| Test environment left running | Scale to 0, notify team |
| Old deployment (no traffic) | Verify unused, then delete |
| Scheduled job service | Check CloudWatch Events, may be intentional |
| Canary/warm standby | May be intentional, add tag to exclude |

## Exclusion Tags

Add these tags to exclude services from detection:

```
whisper:exclude = true
whisper:reason = "Intentional warm standby"
```
