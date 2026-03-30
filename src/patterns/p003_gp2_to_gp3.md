# p003_gp2_to_gp3

**Pattern ID:** p003  
**Name:** GP2 to GP3 Migration  
**Severity:** Medium | **Savings:** ~20%

## What It Detects

EBS gp2 volumes that could be migrated to gp3 for ~20% cost reduction.

| Metric | Value |
|--------|-------|
| gp2 | $0.10/GB/month |
| gp3 | $0.08/GB/month |
| Savings | 20% (~$2/100GB/month) |

## Why It Matters

- **Performance:** gp3 provides IOPS-provisioning independent of size
- **Cost:** 20% cheaper, same performance

## Agent Actions

```bash
python whisper.py scan --pattern 003 --json
python whisper.py fix 003 vol-12345678 --dry-run
```

## Fix Process

1. Create snapshot of gp2 volume
2. Create new gp3 volume from snapshot
3. Attach to instance
4. Delete old gp2 volume

## Safety

- ⚠️ **Requires instance downtime** or careful attach/detach
- Always snapshot first