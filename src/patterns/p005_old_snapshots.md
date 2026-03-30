# p005_old_snapshots

**Pattern ID:** p005  
**Name:** Old EBS Snapshots  
**Severity:** Low-Medium

## What It Detects

EBS snapshots older than threshold (default 90 days) that may no longer be needed.

| Metric | Value |
|--------|-------|
| Cost | ~$0.05/GB/month for snapshot storage |
| Age Threshold | 90 days (configurable) |

## Why It Matters

- **Accumulation:** Snapshots accumulate over time, rarely cleaned up
- **Cost:** Can grow to significant storage costs
- **Risk:** Old snapshots may reference old/obsolete data

## Agent Actions

```bash
python whisper.py scan --pattern 005 --json
python whisper.py fix 005 snap-12345678 --dry-run
```

## Safety

- ⚠️ **Manual review:** Verify source volume still exists/needed
- ✅ **Auto-safe:** If parent volume deleted, snapshot is orphaned anyway