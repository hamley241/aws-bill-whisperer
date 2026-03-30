# p020_secrets_manager

**Pattern ID:** p020  
**Name:** Unused Secrets Manager Secrets  
**Severity:** Low

## What It Detects

Secrets Manager secrets that may be unused or poorly configured:
1. **Unused secrets** - Not accessed in 90+ days
2. **No rotation** - Active secrets without rotation enabled (security risk)

| Metric | Value |
|--------|-------|
| Cost per Secret | $0.40/month |
| API Calls | $0.05 per 10,000 calls |
| Access Threshold | 90 days |

## Why It Matters

- **Low Cost, High Volume:** $0.40/secret adds up with hundreds of secrets
- **Security Hygiene:** Old secrets = potential attack vectors
- **Compliance:** Many frameworks require rotation
- **Clutter:** Unused secrets make audits harder

## Cost Estimation

```python
# Secrets Manager Pricing
SECRET_COST = $0.40/month per secret
API_COST = $0.05 per 10,000 API calls

# Example: 50 unused secrets
# Monthly waste: 50 × $0.40 = $20/month
# Annual waste: $240/year

# Real impact: Usually combined with security risk
```

## Agent Actions

### CLI Usage

```bash
# Scan for unused/unrotated secrets
python whisper.py scan --pattern 020 --json

# Show human-readable
python whisper.py scan --pattern 020

# Scan specific region
python whisper.py scan --pattern 020 --regions us-east-1
```

### Python Usage

```python
from whisper import get_pattern_by_id

pattern = get_pattern_by_id("020")
findings = pattern().scan()

for f in findings:
    issues = f.metadata.get('issues', [])
    days = f.metadata.get('days_since_access', 0)
    print(f"{f.resource_id}: {', '.join(issues)} ({days}d)")
```

## Fix Workflow

### For Unused Secrets (90+ days)

1. **Verify unused** → Check with application owners
2. **Check dependencies** → Search codebase for secret name
3. **Check CloudTrail** → Any recent GetSecretValue calls?
4. **Schedule deletion** → Use 30-day recovery window

```bash
# Schedule deletion with recovery window
aws secretsmanager delete-secret \
  --secret-id my-unused-secret \
  --recovery-window-in-days 30 \
  --region us-east-1

# If needed, restore within 30 days
aws secretsmanager restore-secret \
  --secret-id my-unused-secret \
  --region us-east-1
```

### For Secrets Without Rotation

1. **Assess risk** → How sensitive is this secret?
2. **Configure rotation Lambda** → AWS provides templates
3. **Test rotation** → Manually trigger first rotation
4. **Monitor** → Ensure applications handle rotation

```bash
# Enable rotation (requires Lambda function)
aws secretsmanager rotate-secret \
  --secret-id my-secret \
  --rotation-lambda-arn arn:aws:lambda:us-east-1:123456789012:function:SecretsManagerRotation \
  --rotation-rules AutomaticallyAfterDays=30 \
  --region us-east-1
```

### Safety Rules

- ✅ **30-day recovery window** - Always use when deleting
- ⚠️ **Verify before delete** - Check with teams
- ✅ **Auto-safe if >180 days** - Very likely unused
- ❌ **Never force-delete** - Always use recovery window

## Integration Points

### For Agents (Rusty, etc.)

```python
from whisper import get_pattern_by_id

def check_secrets_manager():
    pattern = get_pattern_by_id("020")
    findings = pattern().scan()
    
    if findings:
        unused = [f for f in findings if 'not accessed' in str(f.metadata.get('issues', []))]
        no_rotation = [f for f in findings if 'no rotation' in str(f.metadata.get('issues', []))]
        total_cost = sum(f.monthly_cost for f in findings)
        
        return (f"Found {len(unused)} unused secrets, {len(no_rotation)} without rotation. "
                f"Potential savings: ${total_cost:.2f}/mo")
    return "All Secrets Manager secrets are healthy"
```

### Security Report

```python
# Include in security audit
for f in findings:
    if 'no rotation' in f.metadata.get('issues', []):
        report.add_security_finding(
            f"⚠️ Secret '{f.resource_id}' has no rotation configured"
        )
```

## Output Format

```json
{
  "pattern_id": "020",
  "findings": [
    {
      "resource_id": "prod/api/old-key",
      "resource_type": "Secrets Manager Secret",
      "region": "us-east-1",
      "monthly_cost": 0.40,
      "severity": "medium",
      "safe_to_fix": true,
      "fix_command": "aws secretsmanager delete-secret --secret-id prod/api/old-key --recovery-window-in-days 30 --region us-east-1",
      "recommendation": "Secret 'prod/api/old-key' issues: not accessed in 195 days. Consider deleting if no longer needed.",
      "metadata": {
        "secret_arn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:prod/api/old-key-abc123",
        "days_since_access": 195,
        "days_since_modified": 365,
        "rotation_enabled": false,
        "rotation_rules": {},
        "created_date": "2024-01-15T10:30:00+00:00",
        "last_accessed_date": "2024-09-15T14:22:00+00:00",
        "tags": {
          "Environment": "prod",
          "Owner": "api-team"
        },
        "issues": ["not accessed in 195 days"]
      }
    },
    {
      "resource_id": "dev/database/credentials",
      "resource_type": "Secrets Manager Secret",
      "region": "us-east-1",
      "monthly_cost": 0.40,
      "severity": "low",
      "safe_to_fix": false,
      "fix_command": null,
      "recommendation": "Secret 'dev/database/credentials' issues: no rotation configured. Enable rotation for security best practices.",
      "metadata": {
        "days_since_access": 5,
        "rotation_enabled": false,
        "issues": ["no rotation configured"]
      }
    }
  ]
}
```

## Related Patterns

- **p005_old_snapshots** - Similar "old resource" detection
- **p001_unattached_ebs** - Similar "unused resource" pattern
