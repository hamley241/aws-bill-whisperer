# p001_unattached_ebs

**Pattern ID:** p001  
**Name:** Unattached EBS Volumes  
**Severity:** Medium-High

## What It Detects

EBS volumes in `available` state (not attached to any EC2 instance) for 30+ days.

| Metric | Value |
|--------|-------|
| Monthly Cost | ~$0.08/GB (gp3), $0.10/GB (gp2) |
| Detection | CloudWatch `VolumeIdleTime` or `describe_volumes` |
| Age Threshold | 30 days |

## Why It Matters

- **Waste:** ~$8-10/month per 100GB gp3 volume
- **Risk:** Orphaned volumes have no protection if instanceTerminated
- **Opportunity:** Delete or snapshot-and-delete

## Cost Estimation

```python
PRICE_PER_GB = {
    "gp2": 0.10,  # $10/month per 100GB
    "gp3": 0.08,   # $8/month per 100GB
    "io1": 0.125, # $12.50/month per 100GB
    "st1": 0.045, # $4.50/month per 100GB
    "sc1": 0.025, # $2.50/month per 100GB
}
```

## Agent Actions

### CLI Usage

```bash
# Scan for unattached volumes
python whisper.py scan --pattern 001 --json

# Show human-readable
python whisper.py scan --pattern 001

# Preview fix (dry-run)
python whisper.py fix 001 vol-12345678 --dry-run

# Apply fix (with confirmation)
python whisper.py fix 001 vol-12345678
```

### Python Usage

```python
from whisper import get_pattern_by_id

pattern = get_pattern_by_id("001")
findings = pattern().scan()

for f in findings:
    print(f"{f.resource_id}: ${f.monthly_cost:.2f}/mo")
    # Output: vol-0a1b2c3d4e5: $8.00/mo
```

## Fix Workflow

1. **List** → Show orphaned volumes
2. **Check Snapshots** → Has snapshot? If yes → skip (safe)
3. **Snapshot (optional)** → Create snapshot before delete
4. **Delete** → Only if no snapshot AND no recent I/O

### Safety Rules

- ✅ **Auto-safe:** Has recent snapshot → safe to skip
- ⚠️ **Manual review:** No snapshot, age > 30 days → snapshot-first
- ❌ **Never auto-delete:** Production volumes, recent I/O

## Integration Points

### For Agents (Rusty, etc.)

```python
# In agent code
from whisper import get_pattern_by_id

def check_unattached_ebs():
    pattern = get_pattern_by_id("001")
    findings = pattern().scan()
    
    if findings:
        # Notify human
        total_waste = sum(f.monthly_cost for f in findings)
        return f"Found {len(findings)} unattached volumes. ${total_waste:.2f}/mo waste"
    return "No unattached volumes found"
```

### Cron Job

```yaml
# Run weekly
schedule: "0 9 * * 1"
command: python whisper.py scan --pattern 001 --json > findings.json
```

## Output Format

```json
{
  "pattern_id": "001",
  "findings": [
    {
      "resource_id": "vol-0a1b2c3d4e5",
      "resource_type": "EBS",
      "region": "us-east-1",
      "monthly_cost": 8.00,
      "severity": "medium",
      "safe_to_fix": false,
      "recommendation": "Create snapshot then delete, or delete if snapshot exists"
    }
  ]
}
```