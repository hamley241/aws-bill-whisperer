# p007_idle_rds

**Pattern ID:** p007  
**Name:** Idle RDS Instances  
**Severity:** Medium-High

## What It Detects

RDS instances with low connections (<10/day) and CPU utilization (<5%).

| Metric | Value |
|--------|-------|
| Cost | $X/month (depends on instance) |
| Threshold | <5% CPU, <10 connections/day |

## Why It Matters

- **Waste:** Running DBs at full cost with minimal usage
- **Opportunity:** Stop (some DBs support this) or rightsize

## Agent Actions

```bash
python whisper.py scan --pattern 007 --json
python whisper.py fix 007 db-12345678 --dry-run
```

## Fix Options

| Action | Savings | Notes |
|--------|---------|-------|
| Stop | 60-70% | Aurora Serverless, some Aurora |
| Rightsize | Varies | Lower instance class |
| Snapshot & Terminate | 100% | Need backup |