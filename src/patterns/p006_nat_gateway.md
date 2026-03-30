# p006_nat_gateway

**Pattern ID:** p006  
**Name:** NAT Gateway Optimization  
**Severity:** Medium

## What It Detects

NAT Gateways with high data transfer costs that could be optimized.

| Metric | Value |
|--------|-------|
| NAT Gateway | $0.045/GB processed |
| Data Transfer | $0.02/GB (inter-region) |

## Why It Matters

- **Cost:** NAT Gateway data processing adds up fast
- **Optimization:** NAT Gateway is single-AZ; use GWaaS or TGW

## Agent Actions

```bash
python whisper.py scan --pattern 006 --json
```

## Optimization Options

| Solution | Savings | Complexity |
|----------|---------|------------|
| S3 Endpoint | Free | Medium |
| PrivateLink | Medium | High |
| TGW | 40-60% | High |