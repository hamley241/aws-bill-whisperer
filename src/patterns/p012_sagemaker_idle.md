# p012_sagemaker_idle

**Pattern ID:** p012  
**Name:** Idle SageMaker Resources  
**Severity:** High-Critical

## What It Detects

SageMaker resources with zero or near-zero usage:
1. **Endpoints** with no inference traffic (invocations) over 7 days
2. **Notebook instances** that have been running idle for 7+ days

| Resource | Hourly Cost | Monthly (24/7) |
|----------|------------|----------------|
| ml.t3.medium endpoint | ~$0.06/hr | ~$43/mo |
| ml.m5.xlarge endpoint | ~$0.27/hr | ~$194/mo |
| ml.p3.2xlarge endpoint | ~$4.28/hr | ~$3,082/mo |
| ml.g5.xlarge notebook | ~$1.41/hr | ~$1,015/mo |

## Why It Matters

- **Expensive 24/7:** SageMaker charges by the hour, even when idle
- **Dev/test waste:** Endpoints created for testing often get forgotten
- **GPU waste:** GPU instances are especially costly when unused
- **Easy to miss:** No automatic shutdown for unused resources

## Cost Estimation

```python
# Endpoint monthly cost
hourly_cost = 0.269  # ml.m5.xlarge
instance_count = 2
monthly_cost = hourly_cost * 24 * 30 * instance_count
# = $0.269 * 720 * 2 = $387.36/month

# Notebook monthly cost
hourly_cost = 1.408  # ml.g5.xlarge
monthly_cost = hourly_cost * 24 * 30
# = $1.408 * 720 = $1,013.76/month
```

## Agent Actions

### CLI Usage

```bash
# Scan for idle SageMaker resources
python whisper.py scan --pattern 012 --json

# Show human-readable
python whisper.py scan --pattern 012

# Scan specific region
python whisper.py scan --pattern 012 --regions us-east-1 us-west-2
```

### Python Usage

```python
from whisper import get_pattern_by_id

pattern = get_pattern_by_id("012")
findings = pattern().scan()

for f in findings:
    print(f"{f.resource_type}: {f.resource_id}")
    print(f"  Monthly cost: ${f.monthly_cost:.2f}")
    print(f"  Invocations: {f.metadata.get('invocations_7d', 'N/A')}")
```

## Fix Workflow

### For Idle Endpoints

1. **Verify no traffic** → Check CloudWatch Invocations metric
2. **Check dependencies** → Is anything expecting this endpoint?
3. **Delete endpoint** → Removes the endpoint (model artifacts preserved)

```bash
# Delete idle endpoint
aws sagemaker delete-endpoint \
    --endpoint-name my-idle-endpoint \
    --region us-east-1

# Also delete endpoint config if no longer needed
aws sagemaker delete-endpoint-config \
    --endpoint-config-name my-endpoint-config
```

### For Idle Notebooks

1. **Check last activity** → Review LastModifiedTime
2. **Stop notebook** → Preserves data, stops billing
3. **Delete if not needed** → Removes notebook instance

```bash
# Stop notebook (preserves data)
aws sagemaker stop-notebook-instance \
    --notebook-instance-name my-notebook \
    --region us-east-1

# Delete notebook (removes instance)
aws sagemaker delete-notebook-instance \
    --notebook-instance-name my-notebook \
    --region us-east-1
```

### Safety Rules

- ⚠️ **Manual review required:** All findings require human judgment
- ✅ **Endpoints:** Deleting preserves model artifacts in S3
- ✅ **Notebooks:** Stopping preserves data on EBS volume
- ❌ **Never auto-delete:** May have dependencies or scheduled usage

## Integration Points

### For Agents (Rusty, etc.)

```python
# In agent code
from whisper import get_pattern_by_id

def check_sagemaker_waste():
    pattern = get_pattern_by_id("012")
    findings = pattern().scan()
    
    if findings:
        total_waste = sum(f.monthly_cost for f in findings)
        endpoints = [f for f in findings if "Endpoint" in f.resource_type]
        notebooks = [f for f in findings if "Notebook" in f.resource_type]
        
        return f"Found {len(endpoints)} idle endpoints and {len(notebooks)} idle notebooks. " \
               f"${total_waste:.2f}/mo waste."
    return "No idle SageMaker resources found"
```

### Cron Job

```yaml
# Run daily (SageMaker is expensive!)
schedule: "0 8 * * *"
command: python whisper.py scan --pattern 012 --json > findings.json
```

## Output Format

```json
{
  "pattern_id": "012",
  "findings": [
    {
      "resource_id": "fraud-detection-endpoint",
      "resource_type": "SageMaker Endpoint",
      "region": "us-east-1",
      "monthly_cost": 387.36,
      "severity": "high",
      "safe_to_fix": false,
      "recommendation": "Endpoint has 0 total invocations in 7 days. Consider deleting.",
      "metadata": {
        "endpoint_status": "InService",
        "invocations_7d": 0,
        "avg_invocations_per_day": 0.0,
        "instance_types": ["ml.m5.xlarge"],
        "instance_counts": [2]
      }
    },
    {
      "resource_id": "experiment-notebook",
      "resource_type": "SageMaker Notebook Instance",
      "region": "us-east-1",
      "monthly_cost": 1013.76,
      "severity": "critical",
      "safe_to_fix": false,
      "recommendation": "Notebook has been idle for 14 days. Consider stopping.",
      "metadata": {
        "instance_type": "ml.g5.xlarge",
        "idle_days": 14,
        "hourly_cost": 1.408
      }
    }
  ]
}
```

## Common Patterns to Watch

| Pattern | Action |
|---------|--------|
| `-dev-` or `-test-` in name | Delete after experiments |
| GPU instances (`ml.p*`, `ml.g*`) | High priority - very expensive |
| Multiple variants, 0 traffic | Consider serverless inference |
| Long-running notebooks | Use lifecycle configs for auto-shutdown |

## Prevention Tips

1. **Auto-shutdown notebooks:** Use lifecycle configurations
2. **Use serverless inference:** For sporadic traffic patterns
3. **Tag resources:** Add `owner` and `expiry` tags
4. **Set budgets:** CloudWatch alarms for SageMaker spend
