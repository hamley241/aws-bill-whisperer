# p008_s3_lifecycle

**Pattern ID:** p008  
**Name:** S3 Lifecycle Rules  
**Severity:** Medium | **Savings:** High

## What It Detects

S3 buckets without lifecycle policies (objects never expire or move to cheaper tier).

| Tier | Cost/GB/month |
|------|---------------|
| STANDARD | $0.023 |
| STANDARD_IA | $0.0125 |
| GLACIER | $0.004 |

## Why It Matters

- **Savings:** Moving to GLACIER saves 80%
- **Compliance:** Lifecycle policies help with data retention

## Agent Actions

```bash
python whisper.py scan --pattern 008 --json
python whisper.py fix 008 my-bucket --dry-run
```

## Fix: Add Lifecycle Rule

1. Transition to STANDARD_IA after 30 days
2. Transition to GLACIER after 90 days
3. Expire after 365 days

## Safety

- ✅ **Always safe:** Lifecycle policies are non-destructive
- ⚠️ **Check:** Regulatory requirements before expiring