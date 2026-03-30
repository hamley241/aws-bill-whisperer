# p011_cloudwatch_logs

**Pattern ID:** p011  
**Name:** CloudWatch Logs Retention & Storage  
**Severity:** Low-Medium

## What It Detects

CloudWatch Log Groups with:
1. No retention policy (logs kept forever)
2. Excessive retention (>90 days)
3. Using STANDARD storage class when Infrequent Access (IA) is better

| Metric | Value |
|--------|-------|
| Storage Cost (STANDARD) | ~$0.50/GB/month |
| Storage Cost (IA) | ~$0.25/GB/month |
| Recommended Retention | 30-90 days (compliance dependent) |
| IA Recommendation | Logs older than 30 days |

## Why It Matters

- **Hidden costs:** Logs grow silently and can become expensive
- **No default retention:** CloudWatch keeps logs forever by default
- **Storage class savings:** IA class is 50% cheaper for infrequently accessed logs
- **Compliance risk:** Keeping logs forever may violate data retention policies

## Cost Estimation

```python
# Monthly cost calculation
STANDARD_PRICE_PER_GB = 0.50  # $/GB/month
IA_PRICE_PER_GB = 0.25        # $/GB/month

# Example: 100 GB log group
# STANDARD: 100 GB × $0.50 = $50/month
# IA:       100 GB × $0.25 = $25/month (50% savings)
```

## Agent Actions

### CLI Usage

```bash
# Scan for log retention issues
python whisper.py scan --pattern 011 --json

# Show human-readable
python whisper.py scan --pattern 011

# Scan specific region
python whisper.py scan --pattern 011 --regions us-east-1 us-west-2
```

### Python Usage

```python
from whisper import get_pattern_by_id

pattern = get_pattern_by_id("011")
findings = pattern().scan()

for f in findings:
    print(f"{f.resource_id}: ${f.monthly_cost:.2f}/mo")
    print(f"  Issue: {f.metadata.get('issue_type')}")
    print(f"  Storage: {f.metadata.get('stored_gb')} GB")
```

## Fix Workflow

### For Retention Issues (no_retention, excessive_retention)

1. **Identify purpose** → Is the log group still needed?
2. **Check compliance** → What's the required retention period?
3. **Set retention** → Apply appropriate retention policy

```bash
# Set 90-day retention
aws logs put-retention-policy \
    --log-group-name "/aws/lambda/my-function" \
    --retention-in-days 90

# Common retention periods: 1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1827, 3653
```

### For Storage Class Issues (wrong_storage_class)

⚠️ **Cannot change storage class after creation** - must recreate:

1. **Export existing logs** (if needed)
2. **Delete old log group**
3. **Create new log group with IA class**
4. **Update log sources to use new group**

```bash
# Create log group with IA class
aws logs create-log-group \
    --log-group-name "/aws/lambda/my-function-new" \
    --log-group-class INFREQUENT_ACCESS

# Set retention on new group
aws logs put-retention-policy \
    --log-group-name "/aws/lambda/my-function-new" \
    --retention-in-days 90
```

### Safety Rules

- ✅ **Auto-safe:** None (retention changes delete logs)
- ⚠️ **Manual review:** All findings require human judgment
- ❌ **Never auto-delete:** Log groups may contain audit data

## Integration Points

### For Agents (Rusty, etc.)

```python
# In agent code
from whisper import get_pattern_by_id

def check_cloudwatch_logs():
    pattern = get_pattern_by_id("011")
    findings = pattern().scan()
    
    if findings:
        total_waste = sum(f.monthly_cost for f in findings)
        by_type = {}
        for f in findings:
            issue = f.metadata.get('issue_type', 'unknown')
            by_type[issue] = by_type.get(issue, 0) + 1
        
        return f"Found {len(findings)} log group issues. ${total_waste:.2f}/mo waste. " \
               f"Types: {by_type}"
    return "No log retention issues found"
```

### Cron Job

```yaml
# Run weekly
schedule: "0 9 * * 1"
command: python whisper.py scan --pattern 011 --json > findings.json
```

## Output Format

```json
{
  "pattern_id": "011",
  "findings": [
    {
      "resource_id": "/aws/lambda/my-function",
      "resource_type": "CloudWatch Log Group",
      "region": "us-east-1",
      "monthly_cost": 25.00,
      "severity": "medium",
      "safe_to_fix": false,
      "recommendation": "No retention policy set. Logs stored forever (50.00 GB). Set retention to 90 days or less.",
      "metadata": {
        "stored_gb": 50.00,
        "retention_days": "never expire",
        "log_group_class": "STANDARD",
        "issue_type": "no_retention"
      }
    }
  ]
}
```

## Common Log Groups to Check

| Log Group Pattern | Typical Action |
|------------------|----------------|
| `/aws/lambda/*` | Set 14-30 day retention |
| `/aws/api-gateway/*` | Set 30-90 day retention |
| `/aws/rds/*` | Set 90 day retention (audit) |
| `/aws/codebuild/*` | Set 30 day retention |
| `/ecs/*` | Set 14-30 day retention |
| Custom application logs | Varies by compliance needs |
