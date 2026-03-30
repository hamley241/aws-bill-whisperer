# p010_idle_load_balancer

**Pattern ID:** p010  
**Name:** Idle Load Balancers  
**Severity:** Medium-High

## What It Detects

Load balancers (ALB/NLB/CLB) with no registered targets or zero traffic.

| Metric | Value |
|--------|-------|
| ALB | $0.0225/LCU-hour |
| NLB | $0.0225/LCU-hour |
| CLB | $0.025/hour |

## Why It Matters

- **Waste:** LBs charge hourly even with zero traffic
- **Accumulation:** Old LBs from old deployments accumulate

## Agent Actions

```bash
python whisper.py scan --pattern 010 --json
python whisper.py fix 010 arn:aws:elasticloadbalancing:... --dry-run
```

## Fix Options

| Action | Savings | Notes |
|--------|---------|-------|
| Delete | 100% | If not needed |
| Stop traffic | 100% | Keep for DNS |

## Safety

- ⚠️ **Check:** DNS might reference this LB
- ✅ **Auto-safe:** No targets = likely orphaned