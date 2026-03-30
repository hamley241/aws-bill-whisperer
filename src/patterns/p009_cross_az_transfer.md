# p009_cross_az_transfer

**Pattern ID:** p009  
**Name:** Cross-AZ Data Transfer  
**Severity:** Low-Medium

## What It Detects

Resources with high cross-AZ data transfer costs (data processed between AZs).

| Cost | Value |
|------|-------|
| Same AZ | Free |
| Cross-AZ | $0.01/GB |
| Inter-region | $0.02/GB |

## Why It Matters

- **Waste:** Cross-AZ traffic is unnecessary expense
- **Fix:** Deploy resources in same AZ or use local

## Agent Actions

```bash
python whisper.py scan --pattern 009 --json
```

## Optimization

- Deploy in same AZ as consumers
- Use regional resources (S3, DynamoDB) instead of cross-AZ