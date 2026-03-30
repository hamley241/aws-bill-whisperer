# p004_idle_ec2

**Pattern ID:** p004  
**Name:** Idle EC2 Instances  
**Severity:** High

## What It Detects

EC2 instances with <5% average CPU utilization over 14 days.

| Metric | Value |
|--------|-------|
| Cost | $X/month (depends on instance type) |
| Threshold | <5% CPU for 14 days |

## Why It Matters

- **Waste:** Running unused instances = full cost for zero value
- **Opportunity:** Stop (not terminate) to save 60-70%, resume when needed

## Agent Actions

```bash
python whisper.py scan --pattern 004 --json
python whisper.py fix 004 i-12345678 --dry-run  # Preview stop
python whisper.py fix 004 i-12345678              # Stop instance
```

## Fix Options

| Action | Savings | Recovery Time |
|--------|---------|---------------|
| Stop | 60-70% | Minutes |
| Terminate | 100% | Need backup |
| Rightsize | Varies | Instant |

## Safety

- ⚠️ **Check:** Is this expected idle? (batch jobs, cron, etc.)
- ✅ **Auto-safe:** Stop (can restart anytime)
- ❌ **Never:** Terminate without human approval