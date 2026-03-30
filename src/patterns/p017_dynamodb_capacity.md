# p017_dynamodb_capacity

**Pattern ID:** p017  
**Name:** DynamoDB Capacity Mode Optimization  
**Severity:** Medium

## What It Detects

DynamoDB tables running on suboptimal capacity mode:
1. **On-Demand tables with steady traffic** - Could save ~40% with Provisioned
2. **Provisioned tables with <10% utilization** - Overprovisioned, wasting money

| Metric | Value |
|--------|-------|
| Typical Savings | 20-40% on capacity costs |
| Detection | CloudWatch ConsumedReadCapacityUnits/ConsumedWriteCapacityUnits |
| Analysis Period | 7 days |

## Why It Matters

- **Waste:** On-Demand costs ~40% more than Provisioned for steady workloads
- **Overprovisioning:** Paying for capacity you're not using (<10% utilization)
- **Opportunity:** Right-size capacity mode based on actual traffic patterns

## Cost Estimation

```python
# On-Demand Pricing (per request)
ON_DEMAND_WCU = $1.25 / 1,000,000 WCUs
ON_DEMAND_RCU = $0.25 / 1,000,000 RCUs

# Provisioned Pricing (per hour)
PROVISIONED_WCU = $0.00065 / WCU-hour
PROVISIONED_RCU = $0.00013 / RCU-hour

# Example: 100 WCU, 500 RCU steady traffic
# On-Demand: ~$35/month
# Provisioned: ~$21/month (40% savings)
```

## Agent Actions

### CLI Usage

```bash
# Scan for DynamoDB capacity issues
python whisper.py scan --pattern 017 --json

# Show human-readable
python whisper.py scan --pattern 017

# Scan specific region
python whisper.py scan --pattern 017 --regions us-east-1 us-west-2
```

### Python Usage

```python
from whisper import get_pattern_by_id

pattern = get_pattern_by_id("017")
findings = pattern().scan()

for f in findings:
    print(f"{f.resource_id}: ${f.monthly_cost:.2f}/mo savings")
    print(f"  {f.recommendation}")
```

## Fix Workflow

### For On-Demand → Provisioned

1. **Analyze traffic** → Check CloudWatch for 30+ days, not just 7
2. **Plan capacity** → Use recommended values + 20% buffer
3. **Enable auto-scaling** → Critical for production tables
4. **Test** → Apply to non-prod first
5. **Monitor** → Watch for throttling after switch

### For Overprovisioned Tables

1. **Verify low utilization** → Check for periodic spikes
2. **Scale down gradually** → Don't cut more than 50% at once
3. **Enable auto-scaling** → Let AWS manage capacity
4. **Monitor** → Watch ConsumedCapacity vs ProvisionedCapacity

### Safety Rules

- ⚠️ **Never auto-switch capacity mode** - Requires careful planning
- ✅ **Auto-scaling recommended** - Always enable after changes
- ❌ **Don't switch during peak** - Schedule for low-traffic periods

## Integration Points

### For Agents (Rusty, etc.)

```python
from whisper import get_pattern_by_id

def check_dynamodb_capacity():
    pattern = get_pattern_by_id("017")
    findings = pattern().scan()
    
    if findings:
        total_savings = sum(f.monthly_cost for f in findings)
        return f"Found {len(findings)} DynamoDB tables with capacity issues. Potential savings: ${total_savings:.2f}/mo"
    return "All DynamoDB tables optimally configured"
```

### Cron Job

```yaml
# Run weekly
schedule: "0 9 * * 1"
command: python whisper.py scan --pattern 017 --json > dynamodb_findings.json
```

## Output Format

```json
{
  "pattern_id": "017",
  "findings": [
    {
      "resource_id": "my-table",
      "resource_type": "DynamoDB Table",
      "region": "us-east-1",
      "monthly_cost": 45.00,
      "severity": "medium",
      "safe_to_fix": false,
      "recommendation": "Switch to Provisioned capacity (RCU: 120, WCU: 60). Traffic is steady (CV: 0.08/0.12). Save ~38%",
      "metadata": {
        "current_mode": "ON_DEMAND",
        "recommended_mode": "PROVISIONED",
        "avg_rcu": 98.5,
        "avg_wcu": 48.2,
        "cv_rcu": 0.08,
        "cv_wcu": 0.12,
        "savings_pct": 38.2
      }
    }
  ]
}
```

## Related Patterns

- **p004_idle_ec2** - Similar utilization-based detection
- **p007_idle_rds** - Similar capacity optimization for databases
